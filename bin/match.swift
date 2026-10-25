// Match audio clips against a ShazamKit custom catalog, the way the phone app would.
// Usage: swift match.swift <catalog.shazamcatalog> <clip1> [clip2 ...]
// Prints one line per clip: MATCH <title> at <offset>s, or NO-MATCH.
import Foundation
import AVFoundation
import ShazamKit

@main
struct Match {
    static func main() async {
        let args = CommandLine.arguments
        guard args.count >= 3 else {
            print("usage: swift match.swift <catalog.shazamcatalog> <clip> [clip ...]")
            exit(2)
        }
        let catalog = SHCustomCatalog()
        do {
            try catalog.add(from: URL(fileURLWithPath: args[1]))
        } catch {
            print("cannot read catalog: \(error.localizedDescription)")
            exit(1)
        }
        let session = SHSession(catalog: catalog)
        for path in args.dropFirst(2) {
            let name = (path as NSString).lastPathComponent
            do {
                let asset = AVURLAsset(url: URL(fileURLWithPath: path))
                let signature = try await SHSignatureGenerator.signature(from: asset)
                switch await session.result(from: signature) {
                case .match(let match):
                    let item = match.mediaItems.first
                    let title = item?.title ?? "?"
                    let offset = item.map { String(format: "%.1f", $0.matchOffset) } ?? "?"
                    print("MATCH\t\(name)\t\(title)\tat \(offset)s")
                case .noMatch:
                    print("NO-MATCH\t\(name)")
                case .error(let error, _):
                    print("ERROR\t\(name)\t\(error.localizedDescription)")
                @unknown default:
                    print("UNKNOWN\t\(name)")
                }
            } catch {
                print("ERROR\t\(name)\t\(error.localizedDescription)")
            }
        }
    }
}
