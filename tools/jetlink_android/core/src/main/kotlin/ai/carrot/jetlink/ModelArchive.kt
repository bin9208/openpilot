package ai.carrot.jetlink

import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.zip.ZipInputStream

object ModelArchive {
    private fun readBounded(input: InputStream, limit: Int): ByteArray {
        val result = ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            require(result.size().toLong() + count <= limit) { "Archive metadata exceeds limit" }
            result.write(buffer, 0, count)
        }
        return result.toByteArray()
    }

    /** Manifest first, then model.onnx, and optionally a small synthetic benchmark input recording. */
    fun importZip(input: InputStream, modelsRoot: File): ModelPackage {
        modelsRoot.mkdirs()
        val temporary = Files.createTempDirectory(modelsRoot.toPath(), ".import-").toFile()
        require(temporary.canonicalFile.toPath().startsWith(modelsRoot.canonicalFile.toPath()))
        try {
            val text: String
            ZipInputStream(input).use { zip ->
                val manifest = zip.nextEntry
                require(manifest != null && manifest.name == "manifest.json" && !manifest.isDirectory) { "Package must start with manifest.json" }
                text = String(readBounded(zip, ModelPackage.MAX_MANIFEST_BYTES), Charsets.UTF_8)
                val contract = ModelPackage.parse(text, temporary, false)
                require(modelsRoot.usableSpace >= contract.artifactBytes + 64L * 1024 * 1024) { "Insufficient model storage" }
                val model = zip.nextEntry
                require(model != null && model.name == "model.onnx" && !model.isDirectory) { "Unexpected model archive path" }
                File(temporary, "model.onnx").outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    var written = 0L
                    while (true) {
                        val count = zip.read(buffer)
                        if (count < 0) break
                        written += count
                        require(written <= contract.artifactBytes) { "Model archive exceeds manifest size" }
                        output.write(buffer, 0, count)
                    }
                    require(written == contract.artifactBytes) { "Incomplete model archive" }
                }
                val optional = zip.nextEntry
                if (optional != null) {
                    require(optional.name == "benchmark.frames.bin" && !optional.isDirectory) { "Unexpected archive entry" }
                    val frames = readBounded(zip, 16 * 1024 * 1024)
                    val stride = contract.warpedBytes + contract.packedCount * 4
                    require(frames.isNotEmpty() && frames.size % stride == 0) { "Incomplete benchmark input" }
                    require(frames.size / stride <= 100) { "Benchmark package supports at most 100 frames" }
                    File(temporary, "benchmark.frames.bin").writeBytes(frames)
                    require(zip.nextEntry == null) { "Extra archive entries are forbidden" }
                }
            }
            val verified = ModelPackage.parse(text, temporary)
            File(temporary, "manifest.json").writeText(text, Charsets.UTF_8)
            val destination = File(modelsRoot, "${verified.artifactSha}-${verified.frameSkip}")
            if (destination.exists()) {
                val old = File(destination, "manifest.json").readText(Charsets.UTF_8)
                require(old == text) { "This model is already stored with different metadata" }
                return ModelPackage.parse(old, destination)
            }
            Files.move(temporary.toPath(), destination.toPath(), StandardCopyOption.ATOMIC_MOVE)
            return ModelPackage.parse(text, destination)
        } finally {
            if (temporary.exists()) {
                check(temporary.canonicalFile.toPath().startsWith(modelsRoot.canonicalFile.toPath()))
                temporary.deleteRecursively()
            }
        }
    }
}
