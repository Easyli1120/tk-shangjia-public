import Foundation
import Vision
let paths = try JSONDecoder().decode([String].self, from: FileHandle.standardInput.readDataToEndOfFile())
struct OCRText: Codable {
    var text: String
    var alternatives: [String]
}
var output: [String: OCRText] = [:]
for path in paths {
    autoreleasepool {
        do {
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["en-US", "zh-Hans"]
            request.usesLanguageCorrection = false
            request.minimumTextHeight = 0.005
            try VNImageRequestHandler(url: URL(fileURLWithPath: path), options: [:]).perform([request])
            let observations = request.results ?? []
            output[path] = OCRText(
                text: observations.compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n"),
                alternatives: observations.flatMap { $0.topCandidates(3).dropFirst().filter { $0.confidence >= 0.25 }.map { $0.string } })
        } catch { /* Failed images stay unclassified and are retried later. */ }
    }
}
FileHandle.standardOutput.write(try JSONEncoder().encode(output))
