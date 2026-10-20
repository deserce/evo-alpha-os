import Foundation
import Vision
import ImageIO

// One bounded image per process. No image or recognized text is retained on disk.
struct Line: Codable { let text: String; let x: Double; let y: Double; let width: Double; let height: Double }
do {
    guard CommandLine.arguments.count == 2 else { throw NSError(domain: "image_path_required", code: 1) }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = props[kCGImagePropertyPixelWidth] as? Int,
          let height = props[kCGImagePropertyPixelHeight] as? Int,
          width > 0, height > 0, width <= 16000, height <= 16000,
          width * height <= 24000000 else { throw NSError(domain: "invalid_image_dimensions", code: 2) }
    let options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceThumbnailMaxPixelSize: 2200, kCGImageSourceCreateThumbnailWithTransform: true]
    guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
        throw NSError(domain: "invalid_image", code: 3)
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: image).perform([request])
    let lines = (request.results ?? []).compactMap { observation -> Line? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return Line(text: candidate.string, x: box.minX, y: 1 - box.maxY,
                    width: box.width, height: box.height)
    }
    let data = try JSONEncoder().encode(lines)
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data("图片文字识别失败: \(error)".utf8))
    exit(1)
}
