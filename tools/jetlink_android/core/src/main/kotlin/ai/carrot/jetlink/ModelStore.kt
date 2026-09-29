package ai.carrot.jetlink

import java.io.Closeable
import java.io.File
import java.io.RandomAccessFile
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** A raw Jetlink upload is accepted only against an already imported, verified manifest. */
class ModelStore(private val root: File, private val manifest: String) : Closeable {
    private val contract = ModelPackage.parse(manifest, root, verifyArtifact = false)
    private val partial = File(root, ".upload.part")
    private var output: RandomAccessFile? = null
    private var written = 0L

    fun begin(sha256: String, size: Long) {
        require(sha256 == contract.sourceSha && sha256 == contract.artifactSha && size == contract.artifactBytes) {
            "Import the matching prepared model package before USB upload"
        }
        require(root.usableSpace >= size + 64L * 1024 * 1024) { "Insufficient model storage" }
        cancel()
        root.mkdirs()
        output = RandomAccessFile(partial, "rw").apply { setLength(0) }
        written = 0
    }

    fun append(offset: Long, chunk: ByteArray) {
        require(output != null && offset == written && chunk.isNotEmpty() && chunk.size <= 4 * 1024 * 1024)
        require(written + chunk.size <= contract.artifactBytes) { "Model upload exceeds manifest" }
        output!!.write(chunk)
        written += chunk.size
    }

    fun finish(): ModelPackage {
        require(output != null)
        output!!.fd.sync()
        output!!.close()
        output = null
        if (written != contract.artifactBytes || ModelPackage.hash(partial) != contract.artifactSha) {
            partial.delete()
            throw IllegalArgumentException("Uploaded model checksum mismatch")
        }
        contract.modelFile.parentFile.mkdirs()
        Files.move(partial.toPath(), contract.modelFile.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        return ModelPackage.parse(manifest, root)
    }

    fun cancel() { output?.close(); output = null; written = 0; partial.delete() }
    override fun close() = cancel()
}
