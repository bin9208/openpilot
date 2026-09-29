package ai.carrot.jetlink

import java.io.Closeable
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.Executors
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicBoolean
import org.json.JSONArray
import org.json.JSONObject

interface InferenceEngine : Closeable {
    fun prepare(model: ModelPackage)
    fun run(warped: ByteArray, packed: FloatArray, reset: Boolean): FloatArray
}

/** One USB peer. Preparation runs separately so pings and state requests remain responsive. */
class JetlinkSession(
    private val model: ModelPackage,
    private val factory: () -> InferenceEngine,
    private val telemetry: () -> JSONObject = { JSONObject() },
) : Closeable {
    @Volatile var state = "none"; private set
    @Volatile private var detail = ""
    private val closed = AtomicBoolean(false)
    private val engineLock = Any()
    private val loader = Executors.newSingleThreadExecutor { task -> Thread(task, "jetlink-prepare").apply { isDaemon = true } }
    private val store = ModelStore(model.root, model.manifestText)
    private var engine: InferenceEngine? = null
    private var lastSequence = -1L
    private var lastFrame = -1L
    @Volatile private var requested = false
    @Volatile private var generation = 0L
    @Volatile private var engineSequence = 0L
    private val updates = ConcurrentLinkedQueue<Pair<Long, ByteArray>>()
    private var resetNext = true
    var framesServed = 0L; private set
    var lastInferenceMs = 0.0; private set
    fun drainUpdates(): List<ByteArray> = buildList {
        while (true) {
            val update = updates.poll() ?: break
            if (!closed.get() && update.first == generation) add(update.second)
        }
    }

    private fun reply(type: Int, sequence: Long, value: JSONObject) = Wire.message(type, sequence, value.toString().toByteArray(Charsets.UTF_8))
    private fun engineStatus() = JSONObject().apply {
        put("state", state); put("sha256", model.sourceSha); put("chunk", 4 * 1024 * 1024); put("detail", detail)
        if (state == "ready") put("spec", model.wireSpec())
    }

    private fun prepare() {
        if (state == "building" || state == "ready" || closed.get()) return
        state = "building"
        detail = ""
        loader.submit {
            var candidate: InferenceEngine? = null
            try {
                candidate = factory()
                candidate.prepare(model)
                synchronized(engineLock) {
                    if (!closed.get()) { engine = candidate; candidate = null; state = "ready" }
                }
            } catch (_: OutOfMemoryError) {
                if (!closed.get()) { detail = "Insufficient model memory"; state = "failed" }
            } catch (e: Exception) {
                if (!closed.get()) { detail = (e.message ?: "Model preparation failed").take(240); state = "failed" }
            } finally {
                candidate?.close()
                val sessionGeneration = generation
                if (!closed.get() && requested && state in listOf("ready", "failed")) {
                    updates.add(sessionGeneration to reply(4, engineSequence, engineStatus()))
                }
            }
        }
    }

    fun handle(message: ByteArray): List<ByteArray> {
        if (closed.get()) return emptyList()
        val header = Wire.decode(message)
        require(message.size == 32 + header.length.toInt()) { "Message length mismatch" }
        val body = message.copyOfRange(32, message.size)
        if (header.type == 1) {
            generation++; updates.clear()
            lastSequence = header.sequence; lastFrame = -1; requested = false; resetNext = true; store.cancel()
            return listOf(reply(2, header.sequence, JSONObject().apply {
                put("protocol", 2); put("backend", "ort"); put("runtime_version", "1.22.0")
                put("device", "android-arm64"); put("engine_state", state)
                put("loaded", if (state == "ready") model.sourceSha else JSONObject.NULL)
                put("frames_served", framesServed); put("cached_models", JSONArray(listOf(model.sourceSha)))
                put("telemetry", telemetry()); put("sleep_after", 0)
            }))
        }
        if (header.sequence <= lastSequence) return emptyList()
        lastSequence = header.sequence
        fun json(): JSONObject {
            require(body.size <= ModelPackage.MAX_MANIFEST_BYTES) { "JSON request too large" }
            return JSONObject(if (body.isEmpty()) "{}" else String(body, Charsets.UTF_8))
        }
        return try {
            when (header.type) {
                15 -> listOf(Wire.message(16, header.sequence))
                17 -> listOf(reply(18, header.sequence, JSONObject().put("ok", false).put("detail", "Android power-off is unsupported")))
                3 -> {
                    generation++; updates.clear(); engineSequence = header.sequence
                    requested = false
                    val request = json()
                    require(request.getString("sha256") == model.sourceSha && request.getLong("nbytes") == JSONObject(model.manifestText).getJSONObject("source").getLong("bytes")) {
                        "Import the model package requested by the comma"
                    }
                    require(request.optInt("frame_skip", 4) == model.frameSkip) { "Model frame_skip mismatch" }
                    requested = true
                    if (!model.modelFile.isFile) {
                        store.begin(model.sourceSha, model.artifactBytes); state = "need_upload"
                    } else if (state == "none") prepare()
                    listOf(reply(4, header.sequence, engineStatus()))
                }
                5 -> {
                    require(requested && state == "need_upload" && body.size > 8)
                    store.append(ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN).long, body.copyOfRange(8, body.size))
                    emptyList() // Streaming upload chunks intentionally have no acknowledgement.
                }
                6 -> {
                    require(requested && state == "need_upload")
                    val completion = json()
                    require(!completion.has("sha256") || completion.getString("sha256") == model.sourceSha)
                    engineSequence = header.sequence
                    store.finish(); state = "none"; prepare()
                    listOf(reply(4, header.sequence, engineStatus()))
                }
                12 -> listOf(reply(13, header.sequence, telemetry().apply {
                    put("engine_state", state); put("detail", detail); put("loaded", if (state == "ready") model.sourceSha else JSONObject.NULL)
                    put("frames_served", framesServed)
                }))
                8 -> infer(header, body)
                else -> listOf(reply(14, header.sequence, JSONObject().put("error", "unsupported_message").put("detail", "Unsupported request")))
            }
        } catch (e: Exception) {
            store.cancel()
            listOf(reply(14, header.sequence, JSONObject().put("error", "request_failed").put("detail", (e.message ?: "Invalid request").take(240))))
        }
    }

    private fun infer(header: Header, body: ByteArray): List<ByteArray> {
        val b = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN)
        val frame = if (body.size >= 8) b.int.toLong() and 0xffffffffL else 0L
        val flags = if (body.size >= 8) b.int else 0
        fun result(status: Int, values: FloatArray = FloatArray(0), micros: Int = 0): List<ByteArray> {
            val data = ByteBuffer.allocate(20 + values.size * 4).order(ByteOrder.LITTLE_ENDIAN)
            data.putInt(frame.toInt()); data.putInt(status); data.putInt(micros); data.putInt(0); data.putInt(micros)
            values.forEach { data.putFloat(it) }
            val tail = if (status == 0 && flags and 2 != 0) telemetry().toString().toByteArray(Charsets.UTF_8) else byteArrayOf()
            return listOf(Wire.message(9, header.sequence, data.array() + tail))
        }
        if (body.size != 8 + model.warpedBytes + model.packedCount * 4 || flags and 3 != flags) return result(2)
        if (!requested || state != "ready") return result(1)
        if (!resetNext && flags and 1 == 0 && frame <= lastFrame) return result(3)
        val warped = ByteArray(model.warpedBytes); b.get(warped)
        val packed = FloatArray(model.packedCount) { b.float }
        if (packed.any { !it.isFinite() }) return result(4)
        return synchronized(engineLock) {
            if (closed.get()) return@synchronized emptyList()
            val active = engine ?: return@synchronized result(1)
            val started = System.nanoTime()
            val output = try { active.run(warped, packed, resetNext || flags and 1 != 0) }
                catch (_: Exception) { resetNext = true; return@synchronized result(3) }
            if (closed.get()) return@synchronized emptyList()
            lastInferenceMs = (System.nanoTime() - started) / 1e6
            if (output.size != model.outputCount) { resetNext = true; return@synchronized result(2) }
            if (output.any { !it.isFinite() }) { resetNext = true; return@synchronized result(4) }
            resetNext = false; lastFrame = frame; framesServed++
            result(0, output, minOf(lastInferenceMs * 1000, Int.MAX_VALUE.toDouble()).toInt())
        }
    }

    override fun close() {
        if (!closed.compareAndSet(false, true)) return
        updates.clear()
        loader.shutdownNow()
        synchronized(engineLock) { engine?.close(); engine = null }
        store.close()
    }
}
