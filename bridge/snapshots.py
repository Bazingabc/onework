"""Bounded, single-flight read snapshots. Slow source IO never holds the read lock."""
from collections import OrderedDict
from copy import deepcopy
import threading
import time


class ReadSnapshots:
    def __init__(self, generation, *, workers=1, capacity=1, interval=2, max_age=15,
                 clock=time.monotonic, wall=time.time):
        self.generation = generation
        self.workers, self.capacity = workers, capacity
        self.interval, self.max_age = interval, max_age
        self.clock, self.wall = clock, wall
        self.lock = threading.RLock()
        self.entries = OrderedDict()
        self.active = {}
        self.closed = False

    def read(self, key, loader):
        with self.lock:
            now, generation = self.clock(), self.generation()
            entry = self.entries.get(key)
            if entry is None or entry['generation'] != generation:
                if key not in self.entries and len(self.entries) >= self.capacity:
                    self.entries.popitem(last=False)
                entry = dict(generation=generation, value=None, observed=None,
                             updated=None, attempted=None, error=False)
                self.entries[key] = entry
            self.entries.move_to_end(key)
            due = entry['attempted'] is None or now - entry['attempted'] >= self.interval
            if not self.closed and due and key not in self.active and len(self.active) < self.workers:
                entry['attempted'] = now
                worker = threading.Thread(target=self._load, args=(key, entry, loader),
                                          name='onework-snapshot', daemon=True)
                self.active[key] = worker
                worker.start()
            age = None if entry['observed'] is None else max(0, now - entry['observed'])
            state = 'stale' if entry['error'] else 'loading' if entry['value'] is None else (
                'stale' if entry['error'] or age >= self.max_age else 'fresh')
            metadata = {'state': state, 'refreshing': key in self.active,
                        'ageMs': None if age is None else int(age * 1000),
                        'updatedAt': entry['updated']}
            # Entries are replaced, never mutated by a consumer or loader.
            value = entry['value']
        return deepcopy(value), metadata

    def _load(self, key, entry, loader):
        started = self.clock()
        observed_wall = int(self.wall())
        try:
            value, failed = loader(), False
        except Exception:
            value, failed = None, True
        with self.lock:
            if (not self.closed and self.entries.get(key) is entry
                    and entry['generation'] == self.generation()):
                entry['error'] = failed
                if not failed:
                    entry.update(value=value, observed=started, updated=observed_wall)
                # Back off after completion, not after the beginning of a slow call.
                entry['attempted'] = self.clock()
            self.active.pop(key, None)

    def invalidate(self, key):
        with self.lock:
            self.entries.pop(key, None)

    def close(self):
        with self.lock:
            self.closed = True
            self.entries.clear()
            workers = list(self.active.values())
        deadline = time.monotonic() + 2
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
