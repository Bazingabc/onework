package com.oneripple.agentviews;

import android.animation.ObjectAnimator;
import android.animation.ValueAnimator;
import android.app.Activity;
import android.content.Intent;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognizerIntent;
import android.text.Editable;
import android.text.InputType;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.text.format.DateUtils;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int VOICE_REQUEST = 41;
    private static final long REFRESH_INTERVAL_MS = 2_000L;
    private static final long OFFLINE_KEEP_AWAKE_MS = 5 * 60 * 1_000L;
    private static final long SUBMISSION_FEEDBACK_MS = 2_400L;
    private static final String[] QUICK_LITERALS = {"1", "2", "A", "B", "继续"};

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final ApiClient api = new ApiClient();
    private final List<SessionItem> sessions = new ArrayList<>();
    private final Map<String, Long> waitingSince = new HashMap<>();
    private final Map<String, Long> completedAt = new HashMap<>();
    private final Map<String, String> structuredAnswers = new LinkedHashMap<>();
    private final AutoFocusPolicy autoFocusPolicy = new AutoFocusPolicy();
    private final SessionDrafts sessionDrafts = new SessionDrafts();
    private final List<Button> quickButtons = new ArrayList<>();
    private final List<View> pendingActions = new ArrayList<>();

    private DashboardState dashboard = DashboardState.empty();
    private SessionRailAdapter sessionAdapter;
    private HomePane homePane;
    private LinearLayout agentPage;
    private WorkspacePager pager;
    private TextView homeDot, agentDot;
    private int pageIndex;
    private ListView sessionList;
    private TextView railSummary;
    private View railAttentionEdge;
    private View detailAttentionEdge;
    private TextView attentionMetric;
    private TextView runningMetric;
    private TextView completedMetric;
    private TextView connectionLabel;
    private Button newButton;
    private TextView eventBanner;
    private TextView detailTitle;
    private TextView detailStatus;
    private TextView detailMeta;
    private LinearLayout pendingCard;
    private LinearLayout stateCard;
    private TextView contextToggle;
    private LinearLayout messages;
    private ScrollView focusScroll;
    private LinearLayout responseDock;
    private TextView composerHint;
    private EditText composer;
    private Button sendButton;
    private Button voiceButton;

    private volatile String selectedId;
    private SessionItem selectedSession;
    private JSONObject selectedDetail;
    private String pendingRequestId;
    private boolean online;
    private boolean hasSnapshot;
    private boolean snapshotStale = true;
    private String snapshotMessage = "正在同步 Codex 列表";
    private boolean hasEverConnected;
    private boolean started;
    private boolean refreshInFlight;
    private boolean completedCollapsed = true;
    private boolean possibleCollapsed;
    private boolean contextExpanded;
    private boolean voiceActive;
    private boolean submitting;
    private String submittingSessionId;
    private String acceptedSessionId;
    private String acceptedRequestId;
    private long acceptedUntilMs;
    private long offlineSinceMs;
    private ObjectAnimator attentionAnimator;

    private final Runnable poll = this::refresh;
    private final Runnable deferredAutoFocus = this::requestImmediateRefresh;
    private final Runnable hideEvent = () -> eventBanner.setVisibility(View.GONE);
    private final Runnable releaseKeepAwake = () -> {
        if (!online) getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    };
    private Runnable advanceAfterSubmission;
    private ConnectionSettings.Profile displayedConnection;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ConnectionSettings.initialize(this);
        displayedConnection = ConnectionSettings.snapshot();
        getWindow().setStatusBarColor(AgentTheme.BACKGROUND);
        getWindow().setNavigationBarColor(AgentTheme.BACKGROUND);
        buildInterface();
        autoFocusPolicy.onTouch(System.currentTimeMillis());
        macDiscovery=new MacDiscovery(this);
        macDiscovery.start();
    }
    private MacDiscovery macDiscovery;

    @Override
    protected void onStart() {
        super.onStart();
        if (displayedConnection != ConnectionSettings.snapshot()) {
            recreate();
            return;
        }
        started = true;
        autoFocusPolicy.onTouch(System.currentTimeMillis());
        if (online) {
            keepScreenAwake();
        } else if (offlineSinceMs > 0L) {
            long remaining = OFFLINE_KEEP_AWAKE_MS
                    - (System.currentTimeMillis() - offlineSinceMs);
            if (remaining > 0L) {
                getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                main.postDelayed(releaseKeepAwake, remaining);
            }
        }
        requestImmediateRefresh();
        homePane.start();
    }

    @Override
    protected void onStop() {
        started = false;
        homePane.stop();
        main.removeCallbacks(poll);
        main.removeCallbacks(deferredAutoFocus);
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        if (macDiscovery != null) macDiscovery.close();
        main.removeCallbacksAndMessages(null);
        if (attentionAnimator != null) attentionAnimator.cancel();
        io.shutdownNow();
        homePane.close();
        super.onDestroy();
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent event) {
        if (event.getActionMasked() == MotionEvent.ACTION_DOWN
                || event.getActionMasked() == MotionEvent.ACTION_MOVE
                || event.getActionMasked() == MotionEvent.ACTION_UP) {
            autoFocusPolicy.onTouch(System.currentTimeMillis());
            scheduleDeferredAutoFocus();
        }
        return super.dispatchTouchEvent(event);
    }

    private void showPage(int index) {
        pager.show(index);
        updatePageDots();
    }

    private void updatePageDots() {
        int index = pageIndex;
        homeDot.setTextColor(index == 0 ? AgentTheme.RUNNING : AgentTheme.MUTED);
        agentDot.setTextColor(dashboard.attentionCount() + dashboard.possible.size() + dashboard.errors.size() > 0
                ? AgentTheme.ATTENTION : index == 1 ? AgentTheme.RUNNING : AgentTheme.MUTED);
        homeDot.setText(index == 0 ? "●" : "○");
        agentDot.setText(index == 1 ? "●" : "○");
        homeDot.setSelected(index == 0); agentDot.setSelected(index == 1);
        int pending = dashboard.attentionCount() + dashboard.possible.size() + dashboard.errors.size();
        agentDot.setContentDescription("Agent，第 2 页，共 2 页" + (pending > 0 ? "，有 " + pending + " 项待关注" : ""));
    }

    private void buildInterface() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(AgentTheme.BACKGROUND);
        root.setFocusableInTouchMode(true);
        root.addView(buildTopBar(), new LinearLayout.LayoutParams(-1, dp(56)));
        pager = new WorkspacePager(this);
        root.addView(pager, new LinearLayout.LayoutParams(-1, 0, 1f));
        agentPage = new LinearLayout(this);
        agentPage.setOrientation(LinearLayout.VERTICAL);
        agentPage.setPadding(dp(24), dp(12), dp(24), dp(4));
        agentPage.addView(buildAgentToolbar(), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(52)));
        agentPage.addView(buildBody(), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1f));
        homePane = new HomePane(this, () -> showPage(1), id -> {
            if (voiceActive || submitting || homePane.busy) return;
            SessionItem item = dashboard.find(id);
            if (item != null) { showPage(1); selectSession(item, true); }
        });
        pager.addView(homePane, new FrameLayout.LayoutParams(-1, -1));
        pager.addView(agentPage, new FrameLayout.LayoutParams(-1, -1));
        pager.protect(responseDock);
        pager.canNavigate = () -> !voiceActive && !submitting && !homePane.busy;
        pager.onPage = index -> {
            pageIndex = index;
            if (index == 0) {
                root.requestFocus();
                android.view.inputmethod.InputMethodManager keyboard =
                        (android.view.inputmethod.InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
                if (keyboard != null) keyboard.hideSoftInputFromWindow(root.getWindowToken(), 0);
            }
            updatePageDots();
        };
        LinearLayout dots = new LinearLayout(this);
        dots.setGravity(Gravity.CENTER);
        homeDot = text("●", 12, AgentTheme.RUNNING, false);
        agentDot = text("○", 12, AgentTheme.MUTED, false);
        homeDot.setGravity(Gravity.CENTER); agentDot.setGravity(Gravity.CENTER);
        homeDot.setContentDescription("首页，第 1 页，共 2 页");
        agentDot.setContentDescription("Agent，第 2 页，共 2 页");
        homeDot.setOnClickListener(v -> showPage(0)); agentDot.setOnClickListener(v -> showPage(1));
        dots.addView(homeDot, new LinearLayout.LayoutParams(dp(48), dp(48)));
        dots.addView(agentDot, new LinearLayout.LayoutParams(dp(48), dp(48)));
        root.addView(dots);
        showPage(0);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            int left;
            int top;
            int right;
            int bottom;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                android.graphics.Insets safe = insets.getInsets(
                        WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                left = safe.left;
                top = safe.top;
                right = safe.right;
                bottom = safe.bottom;
            } else {
                left = insets.getSystemWindowInsetLeft();
                top = insets.getSystemWindowInsetTop();
                right = insets.getSystemWindowInsetRight();
                bottom = insets.getSystemWindowInsetBottom();
            }
            view.setPadding(left, top, right, bottom);
            return insets;
        });
        setContentView(root);
        showQuietState();
        root.requestApplyInsets();
    }

    private View buildTopBar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(20), dp(8), dp(18), dp(8));
        bar.setBackgroundColor(AgentTheme.SURFACE);

        android.widget.ImageView mark = new android.widget.ImageView(this);
        mark.setImageResource(R.drawable.ic_onework_mark);
        mark.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        LinearLayout.LayoutParams markParams = new LinearLayout.LayoutParams(dp(28), dp(28));
        markParams.rightMargin = dp(10); bar.addView(mark, markParams);
        bar.addView(text("OneWork", 20, AgentTheme.TEXT, true), new LinearLayout.LayoutParams(0, -2, 1));
        connectionLabel = text("●  正在连接 Mac", 12, AgentTheme.MUTED, false);
        connectionLabel.setGravity(Gravity.CENTER_VERTICAL);
        addTopItem(bar, connectionLabel, 16);
        Button connect = OneWorkUi.iconButton(this, "settings", "连接设置与 Wi-Fi 配对");
        connect.setContentDescription("连接设置与 Wi-Fi 配对");
        connect.setOnClickListener(v -> {
            if (!voiceActive && !submitting && !homePane.busy)
                startActivity(new Intent(this, PairingActivity.class));
            else toast("请先等待当前操作结束");
        });
        bar.addView(connect, new LinearLayout.LayoutParams(dp(48), dp(48)));
        return bar;
    }

    private View buildAgentToolbar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.addView(text("Agent", 19, AgentTheme.TEXT, true), new LinearLayout.LayoutParams(0, -2, 1));
        attentionMetric = metric("0 个等你", AgentTheme.MUTED);
        runningMetric = metric("0 个运行中", AgentTheme.MUTED);
        completedMetric = metric("0 个刚完成", AgentTheme.MUTED);
        addTopItem(bar, attentionMetric, 8);
        addTopItem(bar, runningMetric, 8);
        addTopItem(bar, completedMetric, 14);

        newButton = button("＋ 新建", AgentTheme.RUNNING, AgentTheme.TEXT, AgentTheme.RUNNING);
        newButton.setContentDescription(getString(R.string.new_session));
        newButton.setOnClickListener(view -> createSession());
        newButton.setEnabled(false);
        newButton.setAlpha(0.42f);
        bar.addView(newButton, new LinearLayout.LayoutParams(dp(94), dp(44)));
        return bar;
    }

    private void addTopItem(LinearLayout bar, View child, int rightMarginDp) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                dp(38));
        params.rightMargin = dp(rightMarginDp);
        bar.addView(child, params);
    }

    private TextView metric(String value, int color) {
        TextView view = text(value, 12, color, true);
        view.setGravity(Gravity.CENTER);
        view.setPadding(dp(13), 0, dp(13), 0);
        view.setSingleLine(true);
        view.setBackground(rounded(AgentTheme.RAISED, 18, 1, AgentTheme.BORDER));
        return view;
    }

    private View buildBody() {
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.HORIZONTAL);
        body.setBackgroundColor(AgentTheme.BACKGROUND);

        int screenWidthDp = getResources().getConfiguration().screenWidthDp;
        int railWidthDp = Math.max(320, Math.min(400, Math.round(screenWidthDp * 0.30f)));
        FrameLayout railHost = new FrameLayout(this);
        railHost.addView(buildSidebar(), new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        railAttentionEdge = new View(this);
        railAttentionEdge.setBackgroundColor(AgentTheme.ATTENTION);
        railAttentionEdge.setVisibility(View.GONE);
        FrameLayout.LayoutParams railEdgeParams = new FrameLayout.LayoutParams(
                dp(3),
                ViewGroup.LayoutParams.MATCH_PARENT,
                Gravity.END);
        railHost.addView(railAttentionEdge, railEdgeParams);
        body.addView(railHost, new LinearLayout.LayoutParams(
                dp(railWidthDp),
                ViewGroup.LayoutParams.MATCH_PARENT));

        View divider = new View(this);
        divider.setBackgroundColor(AgentTheme.BORDER);
        body.addView(divider, new LinearLayout.LayoutParams(
                dp(1),
                ViewGroup.LayoutParams.MATCH_PARENT));

        FrameLayout detailHost = new FrameLayout(this);
        detailHost.addView(buildDetail(), new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        detailAttentionEdge = new View(this);
        detailAttentionEdge.setBackgroundColor(AgentTheme.ATTENTION);
        detailAttentionEdge.setVisibility(View.GONE);
        detailHost.addView(detailAttentionEdge, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(3),
                Gravity.TOP));
        body.addView(detailHost, new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.MATCH_PARENT,
                1f));
        return body;
    }

    private View buildSidebar() {
        LinearLayout sidebar = new LinearLayout(this);
        sidebar.setOrientation(LinearLayout.VERTICAL);
        sidebar.setPadding(dp(10), dp(12), dp(10), dp(10));
        sidebar.setBackgroundColor(AgentTheme.SURFACE);

        TextView heading = text("任务列表", 17, AgentTheme.TEXT, true);
        heading.setPadding(dp(10), 0, dp(10), 0);
        sidebar.addView(heading);
        railSummary = text("正在同步最近一小时会话", 11, AgentTheme.MUTED, false);
        railSummary.setPadding(dp(10), dp(3), dp(10), dp(8));
        sidebar.addView(railSummary);

        sessionAdapter = new SessionRailAdapter(this);
        sessionList = new ListView(this);
        sessionList.setAdapter(sessionAdapter);
        sessionList.setDivider(null);
        sessionList.setDividerHeight(0);
        sessionList.setSelector(android.R.color.transparent);
        sessionList.setVerticalScrollBarEnabled(false);
        sessionList.setOnItemClickListener((parent, view, position, id) -> {
            SessionRailAdapter.Row row = sessionAdapter.rowAt(position);
            if (row.isSection()) {
                if (row.section == SessionRailAdapter.Section.COMPLETED) {
                    completedCollapsed = !completedCollapsed;
                } else if (row.section == SessionRailAdapter.Section.POSSIBLE) {
                    possibleCollapsed = !possibleCollapsed;
                }
                renderRail();
                return;
            }
            selectSession(row.session, true);
        });
        sidebar.addView(sessionList, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1f));
        return sidebar;
    }

    private View buildDetail() {
        LinearLayout detail = new LinearLayout(this);
        detail.setOrientation(LinearLayout.VERTICAL);
        detail.setPadding(dp(24), dp(16), dp(24), dp(16));
        detail.setBackgroundColor(AgentTheme.SURFACE);

        eventBanner = text("", 13, AgentTheme.BACKGROUND, true);
        eventBanner.setGravity(Gravity.CENTER_VERTICAL);
        eventBanner.setPadding(dp(14), dp(9), dp(14), dp(9));
        eventBanner.setVisibility(View.GONE);
        LinearLayout.LayoutParams eventParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        eventParams.bottomMargin = dp(12);
        detail.addView(eventBanner, eventParams);

        LinearLayout headingRow = new LinearLayout(this);
        headingRow.setOrientation(LinearLayout.HORIZONTAL);
        headingRow.setGravity(Gravity.TOP);
        detailTitle = text("正在连接 Mac…", 20, AgentTheme.TEXT, true);
        detailTitle.setMaxLines(2);
        detailTitle.setEllipsize(TextUtils.TruncateAt.END);
        headingRow.addView(detailTitle, new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f));
        detailStatus = text("连接中", 12, AgentTheme.MUTED, true);
        detailStatus.setGravity(Gravity.CENTER);
        detailStatus.setPadding(dp(12), dp(7), dp(12), dp(7));
        LinearLayout.LayoutParams detailStatusParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                dp(34));
        detailStatusParams.leftMargin = dp(16);
        headingRow.addView(detailStatus, detailStatusParams);
        detail.addView(headingRow);

        detailMeta = text("正在获取最近一小时的 Codex 会话", 12, AgentTheme.MUTED, false);
        detailMeta.setMaxLines(2);
        LinearLayout.LayoutParams metaParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        metaParams.topMargin = dp(5);
        metaParams.bottomMargin = dp(12);
        detail.addView(detailMeta, metaParams);

        LinearLayout focusContent = new LinearLayout(this);
        focusContent.setOrientation(LinearLayout.VERTICAL);
        focusContent.setPadding(0, 0, dp(4), dp(12));

        pendingCard = new LinearLayout(this);
        pendingCard.setOrientation(LinearLayout.VERTICAL);
        pendingCard.setPadding(dp(18), dp(15), dp(18), dp(15));
        pendingCard.setVisibility(View.GONE);
        focusContent.addView(pendingCard, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        stateCard = new LinearLayout(this);
        stateCard.setOrientation(LinearLayout.VERTICAL);
        stateCard.setPadding(dp(18), dp(16), dp(18), dp(16));
        stateCard.setBackground(rounded(AgentTheme.SURFACE, 10, 1, AgentTheme.BORDER));
        focusContent.addView(stateCard, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        contextToggle = text("必要上下文", 12, AgentTheme.MUTED, true);
        contextToggle.setGravity(Gravity.CENTER_VERTICAL);
        contextToggle.setPadding(dp(14), 0, dp(14), 0);
        contextToggle.setMinHeight(dp(48));
        contextToggle.setBackground(rounded(AgentTheme.SURFACE, 8, 1, AgentTheme.BORDER));
        contextToggle.setOnClickListener(view -> {
            contextExpanded = !contextExpanded;
            renderMessages(selectedDetail, selectedSession == null ? "" : selectedSession.preview);
        });
        LinearLayout.LayoutParams toggleParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        toggleParams.topMargin = dp(12);
        focusContent.addView(contextToggle, toggleParams);

        messages = new LinearLayout(this);
        messages.setOrientation(LinearLayout.VERTICAL);
        messages.setPadding(0, dp(8), 0, 0);
        focusContent.addView(messages, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        focusScroll = new ScrollView(this);
        focusScroll.setFillViewport(true);
        focusScroll.setVerticalScrollBarEnabled(false);
        focusScroll.addView(focusContent, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        detail.addView(focusScroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                0,
                1f));

        responseDock = (LinearLayout) buildResponseDock();
        detail.addView(responseDock, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        return detail;
    }

    private View buildResponseDock() {
        LinearLayout dock = new LinearLayout(this);
        dock.setOrientation(LinearLayout.VERTICAL);
        dock.setPadding(dp(14), dp(10), dp(14), dp(12));
        dock.setBackground(rounded(AgentTheme.RAISED, 10, 1, AgentTheme.BORDER));

        LinearLayout dockHeader = new LinearLayout(this);
        dockHeader.setOrientation(LinearLayout.HORIZONTAL);
        dockHeader.setGravity(Gravity.CENTER_VERTICAL);
        composerHint = text("请选择一个 Codex 会话", 11, AgentTheme.MUTED, false);
        dockHeader.addView(composerHint, new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f));
        dock.addView(dockHeader);

        LinearLayout commandRow = new LinearLayout(this);
        commandRow.setOrientation(LinearLayout.HORIZONTAL);
        commandRow.setGravity(Gravity.CENTER_VERTICAL);
        commandRow.setPadding(0, dp(7), 0, 0);

        HorizontalScrollView quickScroll = new HorizontalScrollView(this);
        quickScroll.setHorizontalScrollBarEnabled(false);
        LinearLayout quickRow = new LinearLayout(this);
        quickRow.setOrientation(LinearLayout.HORIZONTAL);
        for (String literal : QUICK_LITERALS) {
            Button quick = button(literal, AgentTheme.RAISED, AgentTheme.TEXT, AgentTheme.BORDER);
            quick.setOnClickListener(view -> sendQuick(literal));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    dp(literal.length() > 1 ? 66 : 48),
                    dp(48));
            params.rightMargin = dp(7);
            quickRow.addView(quick, params);
            quickButtons.add(quick);
        }
        quickScroll.addView(quickRow);
        voiceButton = OneWorkUi.iconButton(this, "voice", getString(R.string.voice_input));
        voiceButton.setContentDescription(getString(R.string.voice_input));
        voiceButton.setOnClickListener(view -> startVoiceInput());
        LinearLayout.LayoutParams voiceParams = new LinearLayout.LayoutParams(dp(48), dp(48));
        voiceParams.leftMargin = dp(2);
        voiceParams.rightMargin = dp(8);

        composer = new EditText(this);
        composer.setTextColor(AgentTheme.TEXT);
        composer.setHintTextColor(AgentTheme.MUTED);
        composer.setHint("发送新指令…");
        composer.setTextSize(14);
        composer.setSingleLine(false);
        composer.setMaxLines(3);
        composer.setInputType(
                InputType.TYPE_CLASS_TEXT
                        | InputType.TYPE_TEXT_FLAG_MULTI_LINE
                        | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        composer.setImeOptions(EditorInfo.IME_ACTION_SEND);
        composer.setPadding(dp(14), dp(9), dp(14), dp(9));
        composer.setBackground(rounded(AgentTheme.SURFACE, 8, 1, AgentTheme.BORDER));
        composer.setOnEditorActionListener((view, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                sendComposer();
                return true;
            }
            return false;
        });
        composer.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence value, int start, int count, int after) {}

            @Override
            public void onTextChanged(CharSequence value, int start, int before, int count) {
                updateSendButtonState();
            }

            @Override
            public void afterTextChanged(Editable value) {}
        });
        LinearLayout.LayoutParams inputParams = new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f);
        inputParams.rightMargin = dp(8);
        commandRow.addView(composer, inputParams);
        commandRow.addView(voiceButton, voiceParams);

        sendButton = button("发送", AgentTheme.RUNNING, AgentTheme.TEXT, AgentTheme.RUNNING);
        sendButton.setContentDescription(getString(R.string.send));
        sendButton.setOnClickListener(view -> sendComposer());
        commandRow.addView(sendButton, new LinearLayout.LayoutParams(dp(78), dp(48)));
        dock.addView(commandRow);
        LinearLayout shortcuts = new LinearLayout(this);
        shortcuts.setGravity(Gravity.CENTER_VERTICAL);
        shortcuts.setPadding(0, dp(10), 0, 0);
        TextView literalHint = text("快捷发送", 12, AgentTheme.MUTED, false);
        LinearLayout.LayoutParams literalParams = new LinearLayout.LayoutParams(dp(72), -2);
        shortcuts.addView(literalHint, literalParams);
        shortcuts.addView(quickScroll, new LinearLayout.LayoutParams(0, dp(48), 1));
        shortcuts.addView(text("点击即发送", 11, AgentTheme.MUTED, false));
        dock.addView(shortcuts);
        return dock;
    }

    private void refresh() {
        if (!started || refreshInFlight) return;
        refreshInFlight = true;
        final String desiredId = selectedId;
        final boolean interactionBlocked = isAutoFocusBlocked() || homePane.busy;
        final boolean onHome = pageIndex == 0;
        final boolean foreground = started;
        final boolean baseline = hasSnapshot;
        final List<SessionItem> before = new ArrayList<>(sessions);
        final Map<String, Long> waitingSnapshot = new HashMap<>(waitingSince);
        final Map<String, Long> completedSnapshot = new HashMap<>(completedAt);
        io.execute(() -> {
            try {
                JSONObject health = api.health();
                if (!health.optBoolean("ok")) {
                    throw new IllegalStateException("Codex app-server 尚未就绪");
                }
                ApiClient.SessionSnapshot snapshot = api.sessionsSnapshot();
                if (snapshot.stale) {
                    main.post(() -> applySnapshotPending(snapshot.message));
                    return;
                }
                List<SessionItem> loaded = snapshot.items;
                List<FocusEvent> focusEvents =
                        FocusEvent.between(before, loaded, baseline);
                autoFocusPolicy.observe(focusEvents);
                AttentionEvent event = AttentionEvent.between(before, loaded, baseline);
                long nowMs = System.currentTimeMillis();
                DashboardState projected = DashboardState.from(
                        loaded,
                        waitingSnapshot,
                        completedSnapshot,
                        nowMs);
                AutoFocusPolicy.Decision focusDecision = autoFocusPolicy.decide(
                        projected,
                        onHome ? null : desiredId,
                        foreground,
                        interactionBlocked,
                        nowMs);
                String targetId;
                if (focusDecision.shouldFocus()) {
                    targetId = focusDecision.sessionId;
                } else if (projected.contains(desiredId)) {
                    targetId = desiredId;
                } else if (desiredId == null) {
                    // There is no reading or draft context to protect. Populate an
                    // empty pane immediately after startup/reconnect instead of
                    // waiting for a second state transition that may never arrive.
                    targetId = projected.initialSelection();
                } else if (interactionBlocked) {
                    targetId = null;
                } else {
                    targetId = projected.recommendedSelection();
                }
                JSONObject detail = null;
                String detailError = null;
                // Commit the list first. Details are fetched only for the visible task.
                JSONObject finalDetail = detail;
                String finalDetailError = detailError;
                String finalTargetId = targetId;
                main.post(() -> applyRefresh(
                        snapshot.message,
                        loaded,
                        finalTargetId,
                        finalDetail,
                        finalDetailError,
                        event,
                        focusDecision));
            } catch (Exception error) {
                String message = readableError(error);
                main.post(() -> markOffline(message));
            } finally {
                main.post(() -> {
                    refreshInFlight = false;
                    if (started) main.postDelayed(poll, REFRESH_INTERVAL_MS);
                });
            }
        });
    }

    private void applyRefresh(
            String snapshotNotice,
            List<SessionItem> loaded,
            String targetId,
            JSONObject detail,
            String detailError,
            AttentionEvent event,
            AutoFocusPolicy.Decision focusDecision) {
        boolean reconnected = offlineSinceMs > 0L;
        String previousSelection = selectedId;
        String previousTitle = selectedSession == null ? "" : selectedSession.title;
        List<SessionItem> before = new ArrayList<>(sessions);
        long nowMs = System.currentTimeMillis();
        updateObservedTimes(before, loaded, nowMs);

        online = true;
        snapshotStale = false;
        snapshotMessage = snapshotNotice;
        hasSnapshot = true;
        hasEverConnected = true;
        offlineSinceMs = 0L;
        main.removeCallbacks(releaseKeepAwake);
        keepScreenAwake();
        newButton.setEnabled(true);
        newButton.setAlpha(1f);

        sessions.clear();
        sessions.addAll(loaded);
        dashboard = DashboardState.from(sessions, waitingSince, completedAt, nowMs);
        homePane.overview(dashboard, online);
        boolean autoFocused = focusDecision.shouldFocus()
                && started
                && !isAutoFocusBlocked()
                && !homePane.busy
                && autoFocusPolicy.commit(focusDecision);
        showPage(autoFocused ? 1 : pageIndex);
        if (focusDecision.shouldFocus() && !autoFocused) {
            targetId = dashboard.contains(previousSelection)
                    ? previousSelection
                    : dashboard.recommendedSelection();
            detail = previousSelection != null && previousSelection.equals(targetId)
                    ? selectedDetail
                    : null;
            detailError = null;
        }
        boolean selectionDisappeared = previousSelection != null
                && targetId == null
                && !containsSession(loaded, previousSelection)
                && !composer.getText().toString().trim().isEmpty();
        boolean selectionChanged =
                previousSelection == null ? targetId != null : !previousSelection.equals(targetId);
        if (selectionChanged) {
            saveComposerDraft(previousSelection);
            contextExpanded = false;
            pendingRequestId = null;
            structuredAnswers.clear();
        }
        selectedId = targetId;
        selectedSession = dashboard.find(targetId);
        if (!selectionChanged && detail == null) detail = selectedDetail;
        selectedDetail = detail;
        if (selectionChanged) restoreComposerDraft(targetId);
        renderTopBar();
        renderRail();
        if (selectedSession == null) {
            showQuietState();
        } else {
            SessionItem rendered = detail == null ? selectedSession : new SessionItem(detail);
            renderSession(rendered, detail);
            if (detailError != null) showInlineDetailError(detailError);
        }

        if (selectionDisappeared) {
            showEvent(AttentionEvent.sessionUnavailable(previousTitle));
        } else if (autoFocused) {
            showEvent(AttentionEvent.autoFocused(focusDecision.event));
        } else if (event.isVisible()) {
            showEvent(event);
        } else if (reconnected) {
            showEvent(AttentionEvent.connectionRestored());
        }
        updateAttentionEdges();
        scheduleDeferredAutoFocus();
        if (pageIndex == 1 && selectedId != null) fetchDetail(selectedId);
    }

    private void applySnapshotPending(String message) {
        online = true;
        snapshotStale = true;
        snapshotMessage = message;
        hasEverConnected = true;
        offlineSinceMs = 0L;
        main.removeCallbacks(releaseKeepAwake);
        keepScreenAwake();
        newButton.setEnabled(false);
        newButton.setAlpha(0.42f);
        main.removeCallbacks(deferredAutoFocus);
        homePane.overview(dashboard, true, message);
        renderTopBar();
        renderRail();
        if (selectedSession == null) showQuietState();
        else renderSession(selectedSession, selectedDetail);
    }

    private void updateObservedTimes(
            List<SessionItem> before,
            List<SessionItem> after,
            long nowMs) {
        Map<String, SessionItem> previous = DashboardState.index(before);
        Set<String> currentIds = new HashSet<>();
        for (SessionItem current : after) {
            currentIds.add(current.id);
            SessionItem old = previous.get(current.id);
            if (current.needsAttention()) {
                if (old == null || !old.needsAttention()) waitingSince.put(current.id, nowMs);
                else if (!waitingSince.containsKey(current.id)) {
                    waitingSince.put(current.id, current.updatedAt > 0
                            ? current.updatedAt * 1_000L
                            : nowMs);
                }
            } else {
                waitingSince.remove(current.id);
            }
            if (old != null && old.isRunning() && current.isIdle()) {
                completedAt.put(current.id, nowMs);
            }
        }
        waitingSince.keySet().retainAll(currentIds);
        completedAt.keySet().retainAll(currentIds);
        completedAt.entrySet().removeIf(
                entry -> nowMs - entry.getValue() > DashboardState.RECENT_COMPLETION_MS);
    }

    private void markOffline(String message) {
        boolean firstOfflineObservation = offlineSinceMs == 0L;
        online = false;
        homePane.overview(dashboard, false);
        if (firstOfflineObservation) offlineSinceMs = System.currentTimeMillis();
        newButton.setEnabled(false);
        newButton.setAlpha(0.42f);
        dashboard = DashboardState.from(
                sessions,
                waitingSince,
                completedAt,
                System.currentTimeMillis());
        renderTopBar();
        renderRail();
        if (selectedSession == null) showQuietState();
        else renderSession(selectedSession, selectedDetail);
        if (firstOfflineObservation) showEvent(AttentionEvent.connectionLost(message));
        main.removeCallbacks(releaseKeepAwake);
        long elapsed = System.currentTimeMillis() - offlineSinceMs;
        main.postDelayed(releaseKeepAwake, Math.max(0L, OFFLINE_KEEP_AWAKE_MS - elapsed));
        updateAttentionEdges();
    }

    private void updateObservedConnectionBadge(TextView badge, String text, int color) {
        badge.setText(text);
        badge.setTextColor(color);
        badge.setBackground(badge == connectionLabel ? null : rounded(AgentTheme.RAISED, 18, 1, color));
    }

    private void renderTopBar() {
        attentionMetric.setText(getString(R.string.attention_count, dashboard.attentionCount()));
        attentionMetric.setTextColor(
                dashboard.attentionCount() > 0 ? AgentTheme.ATTENTION : AgentTheme.MUTED);
        attentionMetric.setBackground(rounded(
                AgentTheme.RAISED,
                18,
                1,
                dashboard.attentionCount() > 0 ? AgentTheme.ATTENTION : AgentTheme.BORDER));

        runningMetric.setText(getString(R.string.running_count, dashboard.runningCount()));
        runningMetric.setTextColor(
                dashboard.runningCount() > 0 ? AgentTheme.RUNNING : AgentTheme.MUTED);
        runningMetric.setBackground(rounded(
                AgentTheme.RAISED,
                18,
                1,
                dashboard.runningCount() > 0 ? AgentTheme.RUNNING : AgentTheme.BORDER));

        completedMetric.setText(getString(
                R.string.completed_count,
                dashboard.recentCompletionCount));
        completedMetric.setTextColor(
                dashboard.recentCompletionCount > 0 ? AgentTheme.SUCCESS : AgentTheme.MUTED);
        completedMetric.setBackground(rounded(
                AgentTheme.RAISED,
                18,
                1,
                dashboard.recentCompletionCount > 0 ? AgentTheme.SUCCESS : AgentTheme.BORDER));

        if (online) {
            updateObservedConnectionBadge(
                    connectionLabel,
                    "●  Mac 已连接 · " + (ConnectionSettings.wifi() ? "Wi-Fi" : "USB"),
                    AgentTheme.SUCCESS);
        } else {
            updateObservedConnectionBadge(
                    connectionLabel,
                    "●  Mac 未连接",
                    AgentTheme.ERROR);
        }
    }

    private void renderRail() {
        long nowMs = System.currentTimeMillis();
        sessionAdapter.replace(
                dashboard,
                selectedId,
                online,
                completedCollapsed,
                possibleCollapsed,
                waitingSince,
                nowMs);
        if (!online) {
            railSummary.setText("离线快照 · 恢复连接后自动同步");
            railSummary.setTextColor(AgentTheme.ERROR);
        } else if (snapshotStale || !snapshotMessage.isEmpty()) {
            railSummary.setText(snapshotMessage);
            railSummary.setTextColor(AgentTheme.MUTED);
        } else if (dashboard.attentionCount() > 0) {
            railSummary.setText("按审批优先、等待时间排序");
            railSummary.setTextColor(AgentTheme.ATTENTION);
        } else if (dashboard.sessionCount() == 0) {
            railSummary.setText(R.string.no_recent_sessions);
            railSummary.setTextColor(AgentTheme.MUTED);
        } else {
            railSummary.setText("所有 Codex 会话状态正常");
            railSummary.setTextColor(AgentTheme.SUCCESS);
        }
    }

    private void selectSession(SessionItem item, boolean fetchDetail) {
        boolean changed = selectedId == null || !selectedId.equals(item.id);
        if (changed) {
            saveComposerDraft(selectedId);
            structuredAnswers.clear();
            pendingRequestId = null;
            selectedDetail = null;
            contextExpanded = false;
            cancelScheduledAdvance();
        }
        selectedId = item.id;
        autoFocusPolicy.consume(item.id);
        selectedSession = item;
        if (changed) restoreComposerDraft(item.id);
        sessionAdapter.setSelectedId(item.id);
        renderSession(item, changed ? null : selectedDetail);
        if (fetchDetail) fetchDetail(item.id);
    }

    private void fetchDetail(String threadId) {
        io.execute(() -> {
            try {
                JSONObject detail = api.session(threadId);
                main.post(() -> {
                    if (!threadId.equals(selectedId)) return;
                    selectedDetail = detail;
                    renderSession(new SessionItem(detail), detail);
                });
            } catch (Exception error) {
                String message = readableError(error);
                main.post(() -> {
                    if (!threadId.equals(selectedId)) return;
                    if (selectedDetail != null) {
                        selectedDetail.remove("_revision");
                    }
                    if (selectedSession != null) renderSession(selectedSession, selectedDetail);
                    if (error instanceof ApiClient.ApiException
                            && "snapshot_loading".equals(((ApiClient.ApiException) error).code)) {
                        composerHint.setText(message);
                    } else showInlineDetailError(message);
                });
            }
        });
    }

    private void renderSession(SessionItem item, JSONObject detail) {
        SessionItem summary = dashboard.find(item.id);
        if (summary != null && detail != null) {
            item = SnapshotFreshness.displayState(summary, new SessionItem(detail));
        }
        selectedSession = item;
        detailTitle.setText(item.title);
        String shownType = online ? item.statusType : "offline";
        String shownLabel = !online ? "离线快照" : snapshotStale ? "状态待更新" :
                detail != null && !detailFresh() ? "详情快照 · 待更新" : item.statusLabel;
        styleStatusBadge(shownLabel, statusColor(shownType));

        StringBuilder meta = new StringBuilder();
        meta.append(item.workspaceLabel()).append("  ·  ").append(item.sourceLabel());
        if (online && item.needsAttention()) {
            meta.append("  ·  ").append(waitingDuration(item.id));
        }
        detailMeta.setText(meta.toString());

        boolean acceptedPending = isAcceptedPending(item);
        if (acceptedPending) renderAcceptedCard();
        else if (item.pending != null) renderPending(item.pending);
        else if (item.hasAttentionCard()) renderInferredAttention(item);
        else renderPending(null);
        renderState(item);
        renderMessages(detail, item.preview);

        boolean approval = item.pending != null
                && !"user_input".equals(item.pending.optString("kind"));
        boolean writable = item.isWritable(online)
                && detailFresh()
                && !approval
                && !submitting
                && !acceptedPending;
        String hint;
        if (!online) hint = "Mac 未连接，写操作已停用";
        else if (snapshotStale) hint = snapshotMessage + " · 暂不可发送";
        else if (!detailFresh()) hint = "正在核实最新详情 · 原输入保留，暂不可发送";
        else if (item.isBusyElsewhere()) hint = "原客户端仍在运行或等待原端输入，暂不可接管";
        else if ("unavailable".equals(item.controlType)) hint = "当前无法接入，请刷新后重试";
        else if (submitting && item.id.equals(submittingSessionId)) hint = "正在提交，请稍候…";
        else if (acceptedPending) hint = "已提交，等待 Agent 恢复运行…";
        else if (approval) hint = "请先处理上方审批";
        else if (item.isAttachable() && item.hasInferredAttention()) hint = "回复会自动接入原 Codex 会话";
        else if (item.isAttachable()) hint = "发送时会自动接入原 Codex 会话";
        else if ("waiting_input".equals(item.statusType)) hint = "选择真实选项，或输入回答";
        else if ("running".equals(item.statusType)) hint = "可追加到当前运行中的任务";
        else hint = "发送会开始新的任务";
        if (item.hasInferredAttention()) composer.setHint("回复这个卡点…");
        else if ("running".equals(item.statusType)) composer.setHint("追加到当前任务…");
        else composer.setHint("发送新指令…");
        setComposerEnabled(writable, hint);
        if (!detailFresh() || !online) {
            for (View action : pendingActions) {
                action.setEnabled(false);
                action.setAlpha(0.42f);
            }
        }
        updateQuickButtons();
        updateAttentionEdges();
    }

    private void styleStatusBadge(String label, int color) {
        detailStatus.setText(getString(R.string.status_with_dot, label));
        detailStatus.setTextColor(color);
        detailStatus.setBackground(rounded(AgentTheme.SURFACE, 16, 1, color));
        detailStatus.setContentDescription("当前状态，" + label);
    }

    private void renderPending(JSONObject pending) {
        pendingCard.removeAllViews();
        pendingActions.clear();
        if (pending == null) {
            pendingCard.setVisibility(View.GONE);
            pendingRequestId = null;
            structuredAnswers.clear();
            return;
        }
        pendingCard.setVisibility(View.VISIBLE);
        String requestId = pending.optString("requestId");
        if (!requestId.equals(pendingRequestId)) {
            pendingRequestId = requestId;
            structuredAnswers.clear();
        }
        String kind = pending.optString("kind");
        int edgeColor = "user_input".equals(kind) ? AgentTheme.ATTENTION : AgentTheme.ERROR;
        pendingCard.setBackground(rounded(AgentTheme.SURFACE, 10, 1, edgeColor));
        if ("user_input".equals(kind)) renderQuestions(pending);
        else renderApproval(pending);
    }

    private void renderInferredAttention(SessionItem item) {
        pendingCard.removeAllViews();
        pendingActions.clear();
        pendingRequestId = null;
        structuredAnswers.clear();
        pendingCard.setVisibility(View.VISIBLE);
        boolean observedProtocol = "required_protocol".equals(item.attentionType);
        boolean required = observedProtocol || "required_inferred".equals(item.attentionType);
        int color = required ? AgentTheme.ATTENTION : AgentTheme.MUTED;
        pendingCard.setBackground(rounded(AgentTheme.SURFACE, 10, 1, color));
        pendingCard.addView(kicker(
                observedProtocol
                        ? "●  Codex 在原客户端等你处理"
                        : required ? "●  Codex 正在等你回复" : "◇  可能需要你的回复",
                color));

        TextView source = text(
                observedProtocol ? "由 Codex Hook 实时捕获" : "从最终回复识别",
                11,
                AgentTheme.MUTED,
                false);
        addTopMargin(pendingCard, source, 6);
        String question = item.attentionQuestion.isEmpty()
                ? "请查看最近一条 Codex 回复并决定是否继续。"
                : item.attentionQuestion;
        TextView questionView = text(question, 17, AgentTheme.TEXT, true);
        questionView.setLineSpacing(dp(2), 1.08f);
        addTopMargin(pendingCard, questionView, 8);

        JSONArray options = item.attention == null
                ? null
                : item.attention.optJSONArray("options");
        if (options != null && options.length() > 0) {
            StringBuilder labels = new StringBuilder("可用快捷回复：");
            for (int index = 0; index < options.length(); index++) {
                String value = options.optString(index);
                if (value.isEmpty()) continue;
                if (labels.length() > 7) labels.append("  ·  ");
                labels.append(value);
            }
            TextView optionView = text(labels.toString(), 12, AgentTheme.ATTENTION, false);
            addTopMargin(pendingCard, optionView, 9);
        }

        if (item.hasInferredAttention() && !item.attentionMessageId.isEmpty()) {
            Button dismiss = button(
                    "不是卡点",
                    AgentTheme.RAISED,
                    AgentTheme.MUTED,
                    AgentTheme.BORDER);
            dismiss.setOnClickListener(view -> dismissAttention(item));
            pendingActions.add(dismiss);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    dp(44));
            params.topMargin = dp(12);
            pendingCard.addView(dismiss, params);
        }
    }

    private void renderQuestions(JSONObject pending) {
        pendingCard.addView(kicker("●  需要你的回答", AgentTheme.ATTENTION));
        JSONArray questions = pending.optJSONArray("questions");
        if (questions == null || questions.length() == 0) {
            TextView fallback = text("Codex 正在等待输入，请在下方输入框回答。", 16, AgentTheme.TEXT, false);
            addTopMargin(pendingCard, fallback, 10);
            return;
        }
        for (int index = 0; index < questions.length(); index++) {
            JSONObject question = questions.optJSONObject(index);
            if (question == null) continue;
            String questionId = question.optString("id");
            TextView header = kicker(
                    question.optString("header", "问题 " + (index + 1)),
                    AgentTheme.ATTENTION);
            addTopMargin(pendingCard, header, index == 0 ? 12 : 18);

            TextView questionText = text(question.optString("question"), 17, AgentTheme.TEXT, true);
            questionText.setLineSpacing(dp(2), 1.08f);
            addTopMargin(pendingCard, questionText, 5);

            JSONArray options = question.optJSONArray("options");
            if (options == null || options.length() == 0) {
                TextView freeForm = text("请在下方输入框填写答案", 12, AgentTheme.MUTED, false);
                addTopMargin(pendingCard, freeForm, 9);
                continue;
            }
            for (int optionIndex = 0; optionIndex < options.length(); optionIndex++) {
                JSONObject option = options.optJSONObject(optionIndex);
                if (option == null) continue;
                String label = option.optString("label");
                boolean selected = label.equals(structuredAnswers.get(questionId));
                LinearLayout choice = optionView(
                        label,
                        option.optString("description"),
                        selected);
                choice.setEnabled(!submitting);
                choice.setAlpha(submitting ? 0.55f : 1f);
                choice.setOnClickListener(view -> chooseAnswer(questionId, label, questions.length()));
                pendingActions.add(choice);
                addTopMargin(pendingCard, choice, 8);
            }
        }
        if (questions.length() > 1) {
            Button submit = button(
                    submitting ? "正在提交…" : "提交所选答案",
                    AgentTheme.ATTENTION,
                    AgentTheme.BACKGROUND,
                    AgentTheme.ATTENTION);
            boolean enabled = allQuestionsAnswered(questions) && !submitting;
            submit.setEnabled(enabled);
            submit.setAlpha(enabled ? 1f : 0.42f);
            submit.setOnClickListener(view -> sendStructuredAnswers());
            pendingActions.add(submit);
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    dp(48));
            params.topMargin = dp(12);
            pendingCard.addView(submit, params);
        }
    }

    private LinearLayout optionView(String label, String description, boolean selected) {
        LinearLayout choice = new LinearLayout(this);
        choice.setOrientation(LinearLayout.VERTICAL);
        choice.setGravity(Gravity.CENTER_VERTICAL);
        choice.setPadding(dp(14), dp(10), dp(14), dp(10));
        choice.setMinimumHeight(dp(54));
        choice.setClickable(true);
        choice.setFocusable(true);
        choice.setBackground(rounded(
                selected ? AgentTheme.ATTENTION_SURFACE : AgentTheme.RAISED,
                8,
                1,
                selected ? AgentTheme.ATTENTION : AgentTheme.BORDER));
        TextView title = text((selected ? "✓  " : "") + label, 14, selected ? AgentTheme.ATTENTION : AgentTheme.TEXT, true);
        choice.addView(title);
        if (!description.trim().isEmpty()) {
            TextView detail = text(description, 12, AgentTheme.MUTED, false);
            addTopMargin(choice, detail, 3);
        }
        choice.setContentDescription(
                label
                        + (description.trim().isEmpty() ? "" : "，" + description)
                        + (selected ? "，已选择" : ""));
        return choice;
    }

    private void renderApproval(JSONObject pending) {
        pendingCard.addView(kicker("●  等待审批", AgentTheme.ERROR));
        String kind = pending.optString("kind");
        String scope;
        switch (kind) {
            case "command": scope = "命令执行 · 仅本轮"; break;
            case "file_change": scope = "文件变更 · 仅本轮"; break;
            case "permissions": scope = "权限提升 · 仅本轮"; break;
            default: scope = "未知审批类型"; break;
        }
        TextView scopeView = text(scope, 12, AgentTheme.MUTED, true);
        addTopMargin(pendingCard, scopeView, 10);
        TextView reason = text(
                pending.optString("reason", "Codex 需要人工确认后才能继续。"),
                17,
                AgentTheme.TEXT,
                true);
        addTopMargin(pendingCard, reason, 5);

        Object command = pending.opt("command");
        if (command != null) {
            TextView commandView = text(String.valueOf(command), 12, AgentTheme.TEXT, false);
            commandView.setTextIsSelectable(true);
            commandView.setPadding(dp(12), dp(10), dp(12), dp(10));
            commandView.setBackground(rounded(AgentTheme.RAISED, 7, 1, AgentTheme.BORDER));
            addTopMargin(pendingCard, commandView, 10);
        }
        if ("permissions".equals(kind)) {
            JSONObject permissions = pending.optJSONObject("permissions");
            TextView permissionsView = text(
                    "请求权限：" + (permissions == null ? "未说明" : permissions.toString()),
                    12,
                    AgentTheme.ERROR,
                    false);
            addTopMargin(pendingCard, permissionsView, 9);
        } else if (!"command".equals(kind) && !"file_change".equals(kind)) {
            TextView unsupported = text("此审批类型需要回到 Mac 处理。", 13, AgentTheme.ERROR, true);
            addTopMargin(pendingCard, unsupported, 10);
            return;
        }

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        Button approve = button(
                submitting ? "正在提交…" : "批准本轮",
                AgentTheme.ATTENTION,
                AgentTheme.BACKGROUND,
                AgentTheme.ATTENTION);
        approve.setEnabled(!submitting);
        approve.setAlpha(submitting ? 0.55f : 1f);
        approve.setOnClickListener(view -> sendApproval("accept"));
        Button decline = button("拒绝", AgentTheme.RAISED, AgentTheme.ERROR, AgentTheme.ERROR);
        decline.setEnabled(!submitting);
        decline.setAlpha(submitting ? 0.55f : 1f);
        decline.setOnClickListener(view -> sendApproval("decline"));
        row.addView(approve, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                dp(48)));
        LinearLayout.LayoutParams declineParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                dp(48));
        declineParams.leftMargin = dp(9);
        row.addView(decline, declineParams);
        pendingActions.add(approve);
        pendingActions.add(decline);
        addTopMargin(pendingCard, row, 12);
    }

    private void renderState(SessionItem item) {
        stateCard.removeAllViews();
        if (item.pending != null || item.hasAttentionCard()) {
            stateCard.setVisibility(View.GONE);
            return;
        }
        stateCard.setVisibility(View.VISIBLE);
        int color;
        String title;
        String body;
        if (!online) {
            color = AgentTheme.ERROR;
            title = "离线快照";
            body = "正在自动重连 Mac。恢复连接并同步权威快照前，所有写操作保持禁用。";
        } else if (item.isBusyElsewhere()) {
            color = AgentTheme.RUNNING;
            title = "原客户端仍在处理";
            body = "该 Codex 会话可能正在运行或等待原端输入。形成可安全接管的最终回复后，即可从平板继续。";
        } else if ("unavailable".equals(item.controlType)) {
            color = AgentTheme.ERROR;
            title = "暂时无法接入";
            body = item.statusMessage.isEmpty()
                    ? "请刷新状态；原会话内容不会被修改。"
                    : item.statusMessage;
        } else if (item.isRunning()) {
            color = AgentTheme.RUNNING;
            title = "任务正在推进";
            body = item.preview.isEmpty() ? "Codex 正在执行当前任务。" : item.preview;
        } else if (item.isIdle()) {
            color = AgentTheme.SUCCESS;
            title = "最近任务已完成";
            body = item.preview.isEmpty() ? "该 Agent 当前空闲，可发送新指令。" : item.preview;
        } else if (item.isError()) {
            color = AgentTheme.ERROR;
            title = "需要恢复";
            body = item.statusMessage.isEmpty()
                    ? "此 Codex 会话暂时不可继续，请刷新状态或检查 Mac Bridge。"
                    : item.statusMessage;
        } else {
            color = AgentTheme.ATTENTION;
            title = "正在等待你的输入";
            body = "Codex 尚未提供结构化问题，可在下方直接回答。";
        }
        stateCard.setBackground(rounded(AgentTheme.SURFACE, 10, 1, color));
        stateCard.addView(kicker("●  " + title, color));
        TextView bodyView = text(body, 14, AgentTheme.TEXT, false);
        bodyView.setMaxLines(item.isRunning() || item.isIdle() ? 4 : 8);
        bodyView.setEllipsize(TextUtils.TruncateAt.END);
        addTopMargin(stateCard, bodyView, 8);
        if (item.isError() && online) {
            Button retry = button("重新同步", AgentTheme.RAISED, AgentTheme.TEXT, AgentTheme.BORDER);
            retry.setOnClickListener(view -> requestImmediateRefresh());
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    dp(48));
            params.topMargin = dp(12);
            stateCard.addView(retry, params);
        }
    }

    private void renderMessages(JSONObject detail, String preview) {
        messages.removeAllViews();
        JSONArray values = detail == null ? null : detail.optJSONArray("messages");
        int count = values == null ? 0 : values.length();
        boolean hasPreview = preview != null && !preview.trim().isEmpty();
        if (count == 0 && !hasPreview) {
            contextToggle.setVisibility(View.GONE);
            messages.setVisibility(View.GONE);
            return;
        }
        contextToggle.setVisibility(View.VISIBLE);
        messages.setVisibility(View.VISIBLE);
        int shown = contextExpanded ? count : Math.min(2, count);
        if (count > 2) {
            contextToggle.setEnabled(true);
            contextToggle.setAlpha(1f);
            contextToggle.setText(contextExpanded
                    ? "必要上下文 · 全部 " + count + " 条   ▴ 收起"
                    : "必要上下文 · 最近 2 条   ▾ 展开全部");
        } else {
            contextToggle.setEnabled(false);
            contextToggle.setAlpha(0.82f);
            contextToggle.setText(getString(
                    R.string.context_summary,
                    count == 0 ? "会话摘要" : count + " 条"));
        }
        if (count == 0) {
            addMessageCard("会话摘要", preview, false);
            return;
        }
        int start = contextExpanded ? 0 : Math.max(0, count - 2);
        for (int index = start; index < count; index++) {
            JSONObject value = values.optJSONObject(index);
            if (value == null) continue;
            boolean user = "user".equals(value.optString("role"));
            addMessageCard(user ? "你" : "Codex", value.optString("text"), user);
        }
    }

    private void addMessageCard(String role, String value, boolean user) {
        LinearLayout wrapper = new LinearLayout(this);
        wrapper.setOrientation(LinearLayout.VERTICAL);
        wrapper.setPadding(dp(14), dp(10), dp(14), dp(11));
        wrapper.setBackground(rounded(
                user ? AgentTheme.RAISED : AgentTheme.SURFACE,
                8,
                1,
                user ? AgentTheme.RUNNING : AgentTheme.BORDER));
        wrapper.addView(kicker(role, user ? AgentTheme.RUNNING : AgentTheme.MUTED));
        TextView body = text(value, 14, AgentTheme.TEXT, false);
        body.setTextIsSelectable(true);
        addTopMargin(wrapper, body, 5);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        params.bottomMargin = dp(8);
        messages.addView(wrapper, params);
    }

    private void showQuietState() {
        selectedSession = null;
        selectedDetail = null;
        pendingRequestId = null;
        structuredAnswers.clear();
        pendingCard.setVisibility(View.GONE);
        contextToggle.setVisibility(View.GONE);
        messages.setVisibility(View.GONE);
        stateCard.setVisibility(View.VISIBLE);
        stateCard.removeAllViews();

        if (!online) {
            detailTitle.setText(R.string.connecting_title);
            styleStatusBadge("未连接", AgentTheme.ERROR);
            detailMeta.setText(R.string.connecting_meta);
            stateCard.setBackground(rounded(AgentTheme.SURFACE, 10, 1, AgentTheme.ERROR));
            stateCard.addView(kicker("●  等待连接", AgentTheme.ERROR));
            TextView body = text(
                    "连接成功后会自动恢复最近一小时会话，不需要重启应用。",
                    14,
                    AgentTheme.TEXT,
                    false);
            addTopMargin(stateCard, body, 8);
        } else if (snapshotStale) {
            detailTitle.setText("正在同步任务");
            styleStatusBadge("数据更新中", AgentTheme.MUTED);
            detailMeta.setText("Mac 已连接；列表在后台读取，不影响飞书待办");
            stateCard.setBackground(rounded(AgentTheme.SURFACE, 10, 1, AgentTheme.BORDER));
            stateCard.addView(text(snapshotMessage, 14, AgentTheme.TEXT, false));
        } else if (dashboard.sessionCount() == 0) {
            detailTitle.setText(R.string.no_recent_sessions);
            styleStatusBadge("安静", AgentTheme.SUCCESS);
            detailMeta.setText(R.string.empty_connected_meta);
            stateCard.setBackground(rounded(AgentTheme.SURFACE, 10, 1, AgentTheme.SUCCESS));
            stateCard.addView(kicker("✓  所有状态正常", AgentTheme.SUCCESS));
            TextView body = text(
                    "点击右上角“＋ 新建”，创建可从平板持续推进的 Codex 会话。",
                    14,
                    AgentTheme.TEXT,
                    false);
            addTopMargin(stateCard, body, 8);
        } else {
            detailTitle.setText(R.string.calm_title);
            styleStatusBadge("无需介入", AgentTheme.SUCCESS);
            detailMeta.setText("从左侧选择会话查看状态；出现新卡点时会自动聚焦");
            stateCard.setBackground(rounded(AgentTheme.SURFACE, 10, 1, AgentTheme.SUCCESS));
            stateCard.addView(kicker("✓  当前没有待处理事项", AgentTheme.SUCCESS));
            TextView body = text(
                    dashboard.runningCount() > 0
                            ? dashboard.runningCount() + " 个 Agent 正在运行。你可以继续专注于 Mac，状态变化会在这里明确提示。"
                            : "最近的 Codex 任务均已结束。需要时可从左侧查看结果或新建任务。",
                    14,
                    AgentTheme.TEXT,
                    false);
            addTopMargin(stateCard, body, 8);
        }
        setComposerEnabled(false, online ? "选择一个 Codex 会话后可响应" : "连接恢复前不可写入");
        updateAttentionEdges();
    }

    private void showInlineDetailError(String message) {
        stateCard.setVisibility(View.VISIBLE);
        stateCard.removeAllViews();
        stateCard.setBackground(rounded(AgentTheme.SURFACE, 10, 1, AgentTheme.ERROR));
        stateCard.addView(kicker("●  详情暂不可用", AgentTheme.ERROR));
        TextView body = text(message, 14, AgentTheme.TEXT, false);
        addTopMargin(stateCard, body, 8);
        Button retry = button("重试", AgentTheme.RAISED, AgentTheme.TEXT, AgentTheme.BORDER);
        retry.setOnClickListener(view -> {
            if (selectedId != null) fetchDetail(selectedId);
        });
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                dp(48));
        params.topMargin = dp(12);
        stateCard.addView(retry, params);
    }

    private void showEvent(AttentionEvent event) {
        if (!event.isVisible()) return;
        main.removeCallbacks(hideEvent);
        if (attentionAnimator != null) attentionAnimator.cancel();
        int fill;
        int foreground;
        switch (event.tone) {
            case ATTENTION:
                fill = AgentTheme.ATTENTION;
                foreground = AgentTheme.BACKGROUND;
                break;
            case POSSIBLE:
                fill = AgentTheme.RAISED;
                foreground = AgentTheme.ATTENTION;
                break;
            case RUNNING:
                fill = AgentTheme.RUNNING;
                foreground = AgentTheme.BACKGROUND;
                break;
            case SUCCESS:
                fill = AgentTheme.SUCCESS;
                foreground = AgentTheme.BACKGROUND;
                break;
            case ERROR:
                fill = AgentTheme.ERROR;
                foreground = AgentTheme.ON_ACCENT;
                break;
            default:
                fill = AgentTheme.RAISED;
                foreground = AgentTheme.TEXT;
                break;
        }
        eventBanner.setText(event.message);
        eventBanner.setTextColor(foreground);
        eventBanner.setBackground(rounded(fill, 8, 0, fill));
        eventBanner.setAlpha(1f);
        eventBanner.setVisibility(View.VISIBLE);
        eventBanner.announceForAccessibility(event.message);

        if (event.tone == AttentionEvent.Tone.ATTENTION && ValueAnimator.areAnimatorsEnabled()) {
            attentionAnimator = ObjectAnimator.ofFloat(
                    eventBanner,
                    View.ALPHA,
                    1f,
                    0.60f,
                    1f,
                    0.60f,
                    1f);
            attentionAnimator.setDuration(1_400L);
            attentionAnimator.start();
        }
        if (!event.persistent) {
            long visibleMs = event.kind == AttentionEvent.Kind.SUBMITTED
                    || event.kind == AttentionEvent.Kind.CONNECTION_RESTORED
                    ? SUBMISSION_FEEDBACK_MS
                    : 5_000L;
            main.postDelayed(hideEvent, visibleMs);
        }
    }

    private void updateAttentionEdges() {
        boolean hasError = !dashboard.errors.isEmpty() || !online;
        boolean hasAttention = dashboard.attentionCount() > 0;
        if (hasError || hasAttention) {
            int color = hasError ? AgentTheme.ERROR : AgentTheme.ATTENTION;
            railAttentionEdge.setBackgroundColor(color);
            railAttentionEdge.setVisibility(View.VISIBLE);
        } else {
            railAttentionEdge.setVisibility(View.GONE);
        }
        boolean selectedNeedsEdge = !online
                || (selectedSession != null
                && (selectedSession.isError() || selectedSession.needsAttention()));
        if (selectedNeedsEdge) {
            detailAttentionEdge.setBackgroundColor(
                    !online || (selectedSession != null && selectedSession.isError())
                            ? AgentTheme.ERROR
                            : AgentTheme.ATTENTION);
            detailAttentionEdge.setVisibility(View.VISIBLE);
        } else {
            detailAttentionEdge.setVisibility(View.GONE);
        }
    }

    private void chooseAnswer(String questionId, String label, int questionCount) {
        if (submitting || !detailFresh()) return;
        structuredAnswers.put(questionId, label);
        if (selectedSession != null) renderPending(selectedSession.pending);
        updateQuickButtons();
        if (questionCount == 1) sendStructuredAnswers();
    }

    private boolean allQuestionsAnswered(JSONArray questions) {
        for (int index = 0; index < questions.length(); index++) {
            JSONObject question = questions.optJSONObject(index);
            if (question != null
                    && !structuredAnswers.containsKey(question.optString("id"))) return false;
        }
        return true;
    }

    private void setComposerEnabled(boolean enabled, String hint) {
        composerHint.setText(hint);
        composer.setEnabled(enabled);
        voiceButton.setEnabled(enabled);
        voiceButton.setAlpha(enabled ? 1f : 0.42f);
        for (Button button : quickButtons) {
            button.setEnabled(enabled);
            button.setAlpha(enabled ? 1f : 0.42f);
        }
        updateSendButtonState();
    }

    private void updateSendButtonState() {
        if (sendButton == null || composer == null) return;
        boolean composerEnabled = composer.isEnabled();
        boolean hasText = !composer.getText().toString().trim().isEmpty();
        sendButton.setEnabled(composerEnabled && hasText);
        sendButton.setAlpha(composerEnabled && hasText ? 1f : 0.42f);
    }

    private void updateQuickButtons() {
        JSONObject pending = selectedSession == null ? null : selectedSession.pending;
        JSONArray questions = pending != null && "user_input".equals(pending.optString("kind"))
                ? pending.optJSONArray("questions")
                : null;
        for (int index = 0; index < quickButtons.size(); index++) {
            Button button = quickButtons.get(index);
            String literal = QUICK_LITERALS[index];
            boolean matches = shortcutMatchesAny(literal, questions);
            styleButton(
                    button,
                    matches ? AgentTheme.ATTENTION_SURFACE : AgentTheme.RAISED,
                    matches ? AgentTheme.ATTENTION : AgentTheme.TEXT,
                    matches ? AgentTheme.ATTENTION : AgentTheme.BORDER);
        }
    }

    private boolean shortcutMatchesAny(String literal, JSONArray questions) {
        if (questions == null) return false;
        for (int questionIndex = 0; questionIndex < questions.length(); questionIndex++) {
            JSONObject question = questions.optJSONObject(questionIndex);
            if (question == null) continue;
            JSONArray options = question.optJSONArray("options");
            if (options == null) continue;
            for (int optionIndex = 0; optionIndex < options.length(); optionIndex++) {
                JSONObject option = options.optJSONObject(optionIndex);
                if (option != null
                        && SessionState.shortcutMatches(literal, option.optString("label"))) return true;
            }
        }
        return false;
    }

    private void createSession() {
        if (!online) {
            toast("Mac Bridge 尚未连接");
            return;
        }
        newButton.setEnabled(false);
        newButton.setAlpha(0.42f);
        io.execute(() -> {
            try {
                SessionItem created = api.createSession();
                main.post(() -> {
                    saveComposerDraft(selectedId);
                    selectedId = created.id;
                    restoreComposerDraft(created.id);
                    contextExpanded = false;
                    showEvent(AttentionEvent.created(created));
                    requestImmediateRefresh();
                });
            } catch (Exception error) {
                String message = readableError(error);
                main.post(() -> toast(message));
            } finally {
                main.post(() -> {
                    newButton.setEnabled(online);
                    newButton.setAlpha(online ? 1f : 0.42f);
                });
            }
        });
    }

    private void sendComposer() {
        String value = composer.getText().toString().trim();
        if (value.isEmpty()) {
            toast("请输入要发送的内容");
            return;
        }
        sendText(value, true);
    }

    private void sendQuick(String literal) {
        if (selectedSession == null || !selectedSession.isWritable(online) || !detailFresh() || submitting) {
            toast("当前会话不可从平板写入");
            return;
        }
        JSONObject pending = selectedSession.pending;
        if (pending != null && "user_input".equals(pending.optString("kind"))) {
            JSONArray questions = pending.optJSONArray("questions");
            if (questions != null) {
                for (int questionIndex = 0; questionIndex < questions.length(); questionIndex++) {
                    JSONObject question = questions.optJSONObject(questionIndex);
                    if (question == null
                            || structuredAnswers.containsKey(question.optString("id"))) continue;
                    JSONArray options = question.optJSONArray("options");
                    if (options == null) continue;
                    for (int optionIndex = 0; optionIndex < options.length(); optionIndex++) {
                        JSONObject option = options.optJSONObject(optionIndex);
                        if (option != null
                                && SessionState.shortcutMatches(literal, option.optString("label"))) {
                            structuredAnswers.put(
                                    question.optString("id"),
                                    option.optString("label"));
                            renderPending(pending);
                            updateQuickButtons();
                            if (allQuestionsAnswered(questions)) sendStructuredAnswers();
                            return;
                        }
                    }
                }
            }
        }
        sendText(literal, false);
    }

    private void sendText(String value, boolean clearComposer) {
        SessionItem item = selectedSession;
        if (item == null || !item.isWritable(online) || !detailFresh() || submitting) {
            toast("当前会话不可从平板写入");
            return;
        }
        final String revision = selectedDetail == null ? "" : selectedDetail.optString("_revision");
        beginSubmission(item, item.isAttachable() ? "正在接入原会话…" : "正在发送…");
        io.execute(() -> {
            try {
                api.sendText(
                        item.id,
                        value,
                        item.attentionMessageId,
                        item.isAttachable() ? item.updatedAt : 0L, revision);
                main.post(() -> finishSubmission(item, clearComposer, false));
            } catch (Exception error) {
                String message = readableError(error);
                boolean changed = error instanceof ApiClient.ApiException
                        && "session_changed".equals(((ApiClient.ApiException) error).code);
                main.post(() -> {
                    failSubmission(item, message);
                    if (changed) requestImmediateRefresh();
                });
            }
        });
    }

    private void dismissAttention(SessionItem item) {
        if (submitting || !detailFresh() || item.attentionMessageId.isEmpty()) return;
        final String revision = selectedDetail == null ? "" : selectedDetail.optString("_revision");
        beginSubmission(item, "正在忽略这条提示…");
        io.execute(() -> {
            try {
                api.dismissAttention(item.id, item.attentionMessageId, revision);
                main.post(() -> {
                    submitting = false;
                    submittingSessionId = null;
                    showEvent(AttentionEvent.dismissed(item));
                    requestImmediateRefresh();
                });
            } catch (Exception error) {
                String message = readableError(error);
                main.post(() -> failSubmission(item, message));
            }
        });
    }

    private void sendStructuredAnswers() {
        SessionItem item = selectedSession;
        if (item == null || !detailFresh() || structuredAnswers.isEmpty() || submitting) {
            if (!submitting) toast("请先选择答案");
            return;
        }
        Map<String, String> answers = new LinkedHashMap<>(structuredAnswers);
        final String revision = selectedDetail == null ? "" : selectedDetail.optString("_revision");
        beginSubmission(item, "正在提交答案…");
        io.execute(() -> {
            try {
                api.sendAnswers(item.id, answers, revision);
                main.post(() -> finishSubmission(item, false, true));
            } catch (Exception error) {
                String message = readableError(error);
                main.post(() -> failSubmission(item, message));
            }
        });
    }

    private void sendApproval(String decision) {
        SessionItem item = selectedSession;
        if (item == null || !detailFresh() || submitting) return;
        final String revision = selectedDetail == null ? "" : selectedDetail.optString("_revision");
        beginSubmission(item, "正在提交审批…");
        io.execute(() -> {
            try {
                api.respondApproval(item.id, decision, revision);
                main.post(() -> finishSubmission(item, false, false));
            } catch (Exception error) {
                String message = readableError(error);
                main.post(() -> failSubmission(item, message));
            }
        });
    }

    private void beginSubmission(SessionItem item, String hint) {
        submitting = true;
        submittingSessionId = item.id;
        renderSession(item, selectedDetail);
        composerHint.setText(hint);
        for (View action : pendingActions) {
            action.setEnabled(false);
            action.setAlpha(0.55f);
        }
    }

    private boolean detailFresh() {
        if (!online || snapshotStale || selectedDetail == null || selectedId == null
                || !selectedId.equals(selectedDetail.optString("id"))
                || selectedDetail.optString("_revision").isEmpty()) return false;
        SessionItem summary = dashboard.find(selectedId);
        if (!SnapshotFreshness.sameState(summary, new SessionItem(selectedDetail))) return false;
        return SnapshotFreshness.usable(selectedDetail.optBoolean("_stale"),
                selectedDetail.optLong("_snapshotAgeMs", -1),
                selectedDetail.optLong("_snapshotReceivedMs"), android.os.SystemClock.elapsedRealtime());
    }

    private void finishSubmission(
            SessionItem item,
            boolean clearComposer,
            boolean clearAnswers) {
        submitting = false;
        submittingSessionId = null;
        acceptedSessionId = item.id;
        acceptedRequestId = pendingRequestId;
        acceptedUntilMs = System.currentTimeMillis() + 15_000L;
        if (clearComposer) {
            sessionDrafts.clear(item.id);
            if (item.id.equals(selectedId)) composer.setText("");
        }
        if (clearAnswers) structuredAnswers.clear();
        renderAcceptedCard();
        setComposerEnabled(false, "已提交，等待 Agent 恢复运行…");
        showEvent(AttentionEvent.submitted(item));
        scheduleAdvance(item.id);
        requestImmediateRefresh();
    }

    private void failSubmission(SessionItem item, String message) {
        submitting = false;
        submittingSessionId = null;
        toast(message);
        if (item.id.equals(selectedId)) renderSession(item, selectedDetail);
    }

    private void renderAcceptedCard() {
        pendingCard.setVisibility(View.VISIBLE);
        pendingCard.removeAllViews();
        pendingCard.setBackground(rounded(AgentTheme.SURFACE, 10, 1, AgentTheme.SUCCESS));
        pendingCard.addView(kicker("✓  已提交", AgentTheme.SUCCESS));
        TextView body = text("Agent 正在恢复运行，状态同步后会移回运行中。", 14, AgentTheme.TEXT, false);
        addTopMargin(pendingCard, body, 7);
    }

    private boolean isAcceptedPending(SessionItem item) {
        if (acceptedSessionId == null || !acceptedSessionId.equals(item.id)) return false;
        JSONObject pending = item.pending;
        String requestId = pending == null ? "" : pending.optString("requestId");
        boolean sameRequest = acceptedRequestId != null
                && acceptedRequestId.equals(requestId)
                && System.currentTimeMillis() < acceptedUntilMs;
        if (!sameRequest) {
            acceptedSessionId = null;
            acceptedRequestId = null;
            acceptedUntilMs = 0L;
        }
        return sameRequest;
    }

    private void scheduleAdvance(String completedSessionId) {
        cancelScheduledAdvance();
        advanceAfterSubmission = () -> {
            if (!completedSessionId.equals(selectedId) || isInteractionBusy()) return;
            String nextId = dashboard.nextAttention(completedSessionId);
            SessionItem next = dashboard.find(nextId);
            if (next != null) selectSession(next, true);
        };
        main.postDelayed(advanceAfterSubmission, SUBMISSION_FEEDBACK_MS);
    }

    private void cancelScheduledAdvance() {
        if (advanceAfterSubmission != null) {
            main.removeCallbacks(advanceAfterSubmission);
            advanceAfterSubmission = null;
        }
    }

    private void startVoiceInput() {
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag());
        intent.putExtra(RecognizerIntent.EXTRA_PROMPT, "说出要发送给 Codex 的内容");
        if (intent.resolveActivity(getPackageManager()) == null) {
            toast("系统没有可用的语音识别服务，请使用键盘输入");
            return;
        }
        voiceActive = true;
        try {
            startActivityForResult(intent, VOICE_REQUEST);
        } catch (Exception error) {
            voiceActive = false;
            toast("无法启动语音识别，请使用键盘输入");
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != VOICE_REQUEST) return;
        voiceActive = false;
        if (resultCode != RESULT_OK || data == null) return;
        ArrayList<String> results = data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
        if (results == null || results.isEmpty()) {
            toast("没有识别到语音内容");
            return;
        }
        composer.setText(results.get(0));
        composer.setSelection(composer.length());
        composer.requestFocus();
    }

    private boolean isInteractionBusy() {
        return voiceActive
                || submitting
                || !structuredAnswers.isEmpty()
                || (composer != null && !composer.getText().toString().trim().isEmpty());
    }

    private boolean isAutoFocusBlocked() {
        return voiceActive || submitting || !structuredAnswers.isEmpty()
                || (pager != null && pager.isInteracting())
                || isTyping();
    }

    private boolean isTyping() {
        if (composer == null || !composer.hasFocus()) return false;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            WindowInsets insets = composer.getRootWindowInsets();
            return insets != null && insets.isVisible(WindowInsets.Type.ime());
        }
        android.graphics.Rect visible = new android.graphics.Rect();
        View root = getWindow().getDecorView();
        root.getWindowVisibleDisplayFrame(visible);
        return root.getHeight() - visible.bottom > dp(160);
    }

    private void saveComposerDraft(String sessionId) {
        if (composer == null || sessionId == null) return;
        sessionDrafts.save(sessionId, composer.getText().toString());
    }

    private void restoreComposerDraft(String sessionId) {
        if (composer == null) return;
        String draft = sessionDrafts.get(sessionId);
        if (!draft.contentEquals(composer.getText())) {
            composer.setText(draft);
            composer.setSelection(composer.length());
        }
    }

    private void scheduleDeferredAutoFocus() {
        main.removeCallbacks(deferredAutoFocus);
        if (!started || !online || isAutoFocusBlocked() || !autoFocusPolicy.hasPending()) return;
        long delayMs = autoFocusPolicy.millisUntilEligible(System.currentTimeMillis());
        if (delayMs > 0L) main.postDelayed(deferredAutoFocus, delayMs);
    }

    private void requestImmediateRefresh() {
        main.removeCallbacks(poll);
        if (!refreshInFlight && started) main.post(poll);
    }

    private void keepScreenAwake() {
        if (started && online) {
            getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        }
    }

    private String waitingDuration(String id) {
        long nowMs = System.currentTimeMillis();
        long since = waitingSince.getOrDefault(id, nowMs);
        long minutes = Math.max(0L, (nowMs - since) / DateUtils.MINUTE_IN_MILLIS);
        if (minutes < 1) return "刚刚开始等待";
        if (minutes < 60) return "已等待 " + minutes + " 分钟";
        return "已等待 " + (minutes / 60) + " 小时 " + (minutes % 60) + " 分";
    }

    private String readableError(Exception error) {
        String message = error.getMessage();
        if (message == null || message.trim().isEmpty()) return "无法连接 Agent Views Bridge";
        if (message.contains("Failed to connect") || message.contains("Connection refused")) {
            return "请确认 Bridge 已启动且 adb reverse 已连接";
        }
        if (message.contains("unexpected end of stream")
                || message.contains("Connection reset")
                || message.contains("connection abort")) {
            return "Bridge 连接已中断，正在自动重试";
        }
        return message;
    }

    private boolean containsSession(List<SessionItem> values, String id) {
        if (id == null) return false;
        for (SessionItem item : values) {
            if (id.equals(item.id)) return true;
        }
        return false;
    }

    private int statusColor(String type) {
        switch (type) {
            case "running": return AgentTheme.RUNNING;
            case "waiting_input":
            case "waiting_approval": return AgentTheme.ATTENTION;
            case "possible_input": return AgentTheme.ATTENTION;
            case "unknown": return AgentTheme.MUTED;
            case "idle": return AgentTheme.SUCCESS;
            case "error":
            case "offline": return AgentTheme.ERROR;
            default: return AgentTheme.MUTED;
        }
    }

    private TextView kicker(String value, int color) {
        TextView view = text(value, 11, color, true);
        view.setLetterSpacing(0.05f);
        return view;
    }

    private TextView text(String value, int sp, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(color);
        if (bold) view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        view.setLineSpacing(0, 1.12f);
        return view;
    }

    private Button button(String label, int fill, int textColor, int strokeColor) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(12);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setAllCaps(false);
        button.setPadding(dp(13), 0, dp(13), 0);
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setMinHeight(dp(48));
        button.setMinimumHeight(dp(48));
        button.setStateListAnimator(null);
        OneWorkUi.style(button, fill, textColor, strokeColor);
        return button;
    }

    private void styleButton(Button button, int fill, int textColor, int strokeColor) {
        OneWorkUi.style(button, fill, textColor, strokeColor);
    }

    private GradientDrawable rounded(int color, int radiusDp, int strokeDp, int strokeColor) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radiusDp));
        if (strokeDp > 0) drawable.setStroke(dp(strokeDp), strokeColor);
        return drawable;
    }

    private void addTopMargin(LinearLayout parent, View child, int marginDp) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(marginDp);
        parent.addView(child, params);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
    }

}
