import AppKit
import Foundation

// Android's vector is the single source of truth, including accent color.
struct Layer { let name:String; let path:NSBezierPath; let color:NSColor }
func decodePath(_ data:String) -> NSBezierPath {
    let tokens=data.replacingOccurrences(of:",",with:" ")
        .replacingOccurrences(of:"([MLQZ])",with:" $1 ",options:.regularExpression)
        .split(whereSeparator:{$0.isWhitespace}).map(String.init)
    let path=NSBezierPath(); var index=0; var current=CGPoint.zero
    func point() -> CGPoint {
        guard index+1<tokens.count, let x=Double(tokens[index]), let y=Double(tokens[index+1]) else { fatalError("Invalid vector coordinate") }
        index+=2; return CGPoint(x:x,y:64-y)
    }
    while index<tokens.count {
        let op=tokens[index]; index+=1
        switch op {
        case "M": current=point(); path.move(to:current)
        case "L": current=point(); path.line(to:current)
        case "Q":
            let control=point(), end=point()
            path.curve(to:end,controlPoint1:CGPoint(x:current.x+2*(control.x-current.x)/3,y:current.y+2*(control.y-current.y)/3),
                controlPoint2:CGPoint(x:end.x+2*(control.x-end.x)/3,y:end.y+2*(control.y-end.y)/3)); current=end
        case "Z": path.close()
        default: fatalError("Unsupported vector command: \(op)")
        }
    }
    return path
}
final class VectorParser:NSObject,XMLParserDelegate {
    var layers=[Layer]()
    func parser(_ parser:XMLParser,didStartElement elementName:String,namespaceURI:String?,qualifiedName:String?,attributes:[String:String]) {
        guard elementName=="path" else { return }
        guard let name=attributes["android:name"], let data=attributes["android:pathData"],
              let fill=attributes["android:fillColor"],fill.count==7,
              let rgb=UInt32(fill.dropFirst(),radix:16) else { fatalError("Expected named, solid-filled vector paths") }
        layers.append(Layer(name:name,path:decodePath(data),color:NSColor(srgbRed:CGFloat((rgb>>16)&255)/255,
            green:CGFloat((rgb>>8)&255)/255,blue:CGFloat(rgb&255)/255,alpha:1)))
    }
}
let parser=XMLParser(data:try Data(contentsOf:URL(fileURLWithPath:CommandLine.arguments[1])))
let delegate=VectorParser(); parser.delegate=delegate
guard parser.parse(),delegate.layers.count==4 else { fatalError("Invalid Perch vector") }
let output=URL(fileURLWithPath:CommandLine.arguments[2],isDirectory:true)
try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
func paintMark(_ size:CGFloat, app:Bool, inverse:Bool=false) {
    NSGraphicsContext.saveGraphicsState()
    if app {
        NSColor(srgbRed:28/255,green:46/255,blue:101/255,alpha:1).setFill()
        NSBezierPath(roundedRect:NSRect(x:size*0.04,y:size*0.04,width:size*0.92,height:size*0.92),xRadius:size*0.205,yRadius:size*0.205).fill()
        let transform=NSAffineTransform(); transform.translateX(by:size*22/108,yBy:size*22/108); transform.scale(by:size/108); transform.concat()
    } else { let transform=NSAffineTransform(); transform.scale(by:size/64); transform.concat() }
    for layer in delegate.layers {
        (app ? (layer.name=="beak" ? layer.color : NSColor.white) : (inverse ? NSColor.white : NSColor.black)).setFill()
        layer.path.fill()
    }
    NSGraphicsContext.restoreGraphicsState()
}
func bitmap(_ width:Int,_ height:Int,_ path:URL,draw:()->Void) throws {
    let rep=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:width,pixelsHigh:height,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
    NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current=NSGraphicsContext(bitmapImageRep:rep)
    draw(); NSGraphicsContext.restoreGraphicsState()
    try rep.representation(using:.png,properties:[:])!.write(to:path)
}
func png(_ size:Int,_ path:URL,app:Bool=true) throws { try bitmap(size,size,path) { paintMark(CGFloat(size),app:app) } }
final class MarkView:NSView {
    override func draw(_ dirtyRect:NSRect) { paintMark(64,app:false) }
}
let view=MarkView(frame:NSRect(x:0,y:0,width:64,height:64))
try view.dataWithPDF(inside:view.bounds).write(to:output.appendingPathComponent("OneWorkMenu.pdf"))
try png(128,output.appendingPathComponent("OneWorkIcon.png"))
let iconset=output.appendingPathComponent("OneWork.iconset")
try FileManager.default.createDirectory(at:iconset,withIntermediateDirectories:true)
for size in [16,32,128,256,512] {
    try png(size,iconset.appendingPathComponent("icon_\(size)x\(size).png"))
    try png(size*2,iconset.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
for size in [16,18,22,32,36,44] { try png(size,output.appendingPathComponent("menu-\(size).png"),app:false) }
// QA sheet: enlarged silhouette plus real pixel-size light/dark menu samples.
try bitmap(880,320,output.appendingPathComponent("Perch-preview.png")) {
    NSColor(srgbRed:0.96,green:0.96,blue:0.95,alpha:1).setFill(); NSRect(x:0,y:0,width:880,height:320).fill()
    func at(_ x:CGFloat,_ y:CGFloat,_ size:CGFloat,_ app:Bool,_ inverse:Bool=false) {
        NSGraphicsContext.saveGraphicsState(); let t=NSAffineTransform(); t.translateX(by:x,yBy:y); t.concat()
        paintMark(size,app:app,inverse:inverse); NSGraphicsContext.restoreGraphicsState()
    }
    at(30,55,230,true); at(275,70,180,false)
    NSColor(srgbRed:0.12,green:0.16,blue:0.23,alpha:1).setFill(); NSRect(x:510,y:70,width:330,height:64).fill()
    for (i,size) in [16,18,22,32].enumerated() {
        at(CGFloat(535+i*75),170,CGFloat(size),false)
        at(CGFloat(535+i*75),85,CGFloat(size),false,true)
        ("\(size) px" as NSString).draw(at:NSPoint(x:CGFloat(527+i*75),y:140),withAttributes:[.font:NSFont.systemFont(ofSize:12),.foregroundColor:NSColor.darkGray])
    }
    ("OneWork / Perch" as NSString).draw(at:NSPoint(x:40,y:275),withAttributes:[.font:NSFont.systemFont(ofSize:20,weight:.medium),.foregroundColor:NSColor.black])
}
