package ai.carrot.jetlink

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.file.Files
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import org.junit.Assert.*
import org.junit.Test

class AppLifecycleTest {
    @Test fun lateAndDuplicateUsbPermissionCannotStartSessions() {
        val gate = PermissionGate()
        val first = gate.begin(7)
        gate.cancel()
        assertFalse(gate.consume(first, 7))
        val second = gate.begin(8)
        assertFalse(gate.consume(first, 7))
        assertFalse(gate.consume(second, 7))
        assertTrue(gate.consume(second, 8))
        assertFalse(gate.consume(second, 8))
    }

    @Test fun statsRetainAllDeadlineMissesAndRejectNan() {
        val stats = FrameStats(3)
        for (ms in listOf(1.0, 10.0, 50.0, 60.0)) stats.record(ms)
        val snapshot = stats.snapshot()
        assertEquals(4L, snapshot.frames)
        assertEquals(1L, snapshot.overBudget)
        assertEquals(50.0, snapshot.p50, 0.0)
        assertEquals(60.0, snapshot.maximum, 0.0)
        assertThrows(IllegalArgumentException::class.java) { stats.record(Double.NaN) }
        assertEquals(4L, stats.snapshot().frames)
    }

    private fun archive(manifest: String, path: String = "model.onnx", model: ByteArray = byteArrayOf(1,2,3)): ByteArray {
        val bytes = ByteArrayOutputStream()
        ZipOutputStream(bytes).use { zip ->
            zip.putNextEntry(ZipEntry("manifest.json")); zip.write(manifest.toByteArray()); zip.closeEntry()
            zip.putNextEntry(ZipEntry(path)); zip.write(model); zip.closeEntry()
        }
        return bytes.toByteArray()
    }

    @Test fun modelArchiveImportIsAtomicAndRejectsPathEscape() {
        val root = Files.createTempDirectory("archive-test").toFile()
        try {
            val source = File(root,"source")
            val text = ModelStoreTest().fixture(source)
            val models = File(root,"models")
            val good = ModelArchive.importZip(ByteArrayInputStream(archive(text)), models)
            assertArrayEquals(byteArrayOf(1,2,3), good.modelFile.readBytes())
            assertThrows(IllegalArgumentException::class.java) {
                ModelArchive.importZip(ByteArrayInputStream(archive(text,"../outside")),models)
            }
            assertFalse(File(root,"outside").exists())
            assertThrows(IllegalArgumentException::class.java) {
                ModelArchive.importZip(ByteArrayInputStream(archive(text,model=byteArrayOf(7,8,9))),models)
            }
            assertArrayEquals(byteArrayOf(1,2,3), good.modelFile.readBytes())
        } finally { root.deleteRecursively() }
    }
}
