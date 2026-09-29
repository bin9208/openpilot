package ai.carrot.jetlink

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class JetlinkSessionTest {
    private fun json(message: ByteArray) = JSONObject(String(message.copyOfRange(32, message.size), Charsets.UTF_8))
    private fun request(pkg: ModelPackage, seq: Long = 2) = Wire.message(3, seq, JSONObject().apply {
        put("sha256", pkg.sourceSha); put("nbytes", pkg.artifactBytes); put("frame_skip", pkg.frameSkip)
    }.toString().toByteArray())

    @Test fun pingDuringPrepareAndShutdownRejection() {
        val root = Files.createTempDirectory("jetlink-session").toFile()
        val started = CountDownLatch(1); val release = CountDownLatch(1)
        try {
            val pkg = ModelPackage.parse(ModelStoreTest().fixture(root), root)
            JetlinkSession(pkg, { object : InferenceEngine {
                override fun prepare(model: ModelPackage) { started.countDown(); release.await(2, TimeUnit.SECONDS) }
                override fun run(warped: ByteArray, packed: FloatArray, reset: Boolean) = FloatArray(4)
                override fun close() {}
            } }).use { session ->
                assertEquals("building", json(session.handle(request(pkg)).single()).getString("state"))
                assertTrue(started.await(1, TimeUnit.SECONDS))
                val t = System.nanoTime()
                assertEquals(16, Wire.decode(session.handle(Wire.message(15, 3)).single()).type)
                assertTrue((System.nanoTime() - t) < 500_000_000L)
                assertFalse(json(session.handle(Wire.message(17, 4, "{}".toByteArray())).single()).getBoolean("ok"))
                release.countDown()
            }
        } finally { release.countDown(); root.deleteRecursively() }
    }

    @Test fun replayDoesNotRunModelTwiceAndNonfiniteIsRejected() {
        val root = Files.createTempDirectory("jetlink-session").toFile()
        val runs = AtomicInteger()
        try {
            val pkg = ModelPackage.parse(ModelStoreTest().fixture(root), root)
            JetlinkSession(pkg, { object : InferenceEngine {
                override fun prepare(model: ModelPackage) {}
                override fun run(warped: ByteArray, packed: FloatArray, reset: Boolean): FloatArray {
                    runs.incrementAndGet(); return floatArrayOf(Float.NaN, 0f, 0f, 0f)
                }
                override fun close() {}
            } }).use { session ->
                session.handle(request(pkg))
                val until = System.nanoTime() + 1_000_000_000L
                while (session.state != "ready" && System.nanoTime() < until) Thread.sleep(1)
                assertEquals("ready", session.state)
                val data = ByteBuffer.allocate(8 + pkg.warpedBytes + pkg.packedCount * 4).order(ByteOrder.LITTLE_ENDIAN)
                data.putInt(10); data.putInt(1)
                val message = Wire.message(8, 3, data.array())
                val response = session.handle(message).single()
                assertEquals(9, Wire.decode(response).type)
                assertEquals(4, ByteBuffer.wrap(response).order(ByteOrder.LITTLE_ENDIAN).getInt(36))
                assertTrue(session.handle(message).isEmpty())
                assertEquals(1, runs.get())
            }
        } finally { root.deleteRecursively() }
    }

    @Test fun closedSessionDiscardsLatePreparation() {
        val root = Files.createTempDirectory("jetlink-session").toFile()
        val started = CountDownLatch(1); val release = CountDownLatch(1); val closed = CountDownLatch(1)
        try {
            val pkg = ModelPackage.parse(ModelStoreTest().fixture(root), root)
            val session = JetlinkSession(pkg, { object : InferenceEngine {
                override fun prepare(model: ModelPackage) { started.countDown(); try { release.await() } catch (_: InterruptedException) {} }
                override fun run(warped: ByteArray, packed: FloatArray, reset: Boolean) = FloatArray(4)
                override fun close() { closed.countDown() }
            } })
            session.handle(request(pkg)); assertTrue(started.await(1, TimeUnit.SECONDS))
            session.close(); release.countDown()
            assertTrue(closed.await(1, TimeUnit.SECONDS))
            assertTrue(session.handle(Wire.message(15, 9)).isEmpty())
        } finally { release.countDown(); root.deleteRecursively() }
    }
}
