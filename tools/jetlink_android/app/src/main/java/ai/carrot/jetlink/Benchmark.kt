package ai.carrot.jetlink

import android.content.Context
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.json.JSONObject

object Benchmark {
    fun start(context: Context) {
        if (AppState.busy) return
        AppState.busy = true; AppState.phase = "preparing"; AppState.detail = ""
        AppState.benchmarkCancelled = false; AppState.stats = FrameStats()
        val backend = AppState.backend
        val recordingFile = File(context.cacheDir, "parity.zip")
        recordingFile.delete()
        Thread({
            try {
                val model = ModelRepository.current(context, true) ?: error(context.getString(R.string.no_model))
                val stride = model.warpedBytes + 4 * model.packedCount
                val recording = File(model.root, "benchmark.frames.bin")
                val bytes = if (recording.isFile) {
                    require(recording.length() in stride.toLong()..16L * 1024 * 1024 && recording.length() % stride == 0L)
                    recording.readBytes()
                } else null
                val warped = ByteArray(model.warpedBytes)
                val packed = FloatArray(model.packedCount)
                val desire = model.inputs.getValue("desire").elements
                packed[desire + 1] = 1f
                val action = desire + model.inputs.getValue("traffic_convention").elements
                packed[action] = 0.1f; packed[action + 1] = 0.3f
                val inputBytes = bytes ?: ByteBuffer.allocate(stride).order(ByteOrder.LITTLE_ENDIAN).apply {
                    put(warped); packed.forEach { putFloat(it) }
                }.array()
                val recordedFrames = inputBytes.size / stride
                val recorder = OutputRecording(model.artifactSha, model.sourceSha, inputBytes, recordedFrames,
                    model.outputCount, JSONObject(model.manifestText).getJSONObject("output_slices"), backend.name)
                OrtEngine(backend).use { engine ->
                    if (AppState.benchmarkCancelled) return@use
                    if (DeviceHealth.tooHot(context)) error(context.getString(R.string.thermal_stop))
                    engine.prepare(model)
                    AppState.phase = "benchmarking"
                    repeat(100) { frame ->
                        if (AppState.benchmarkCancelled) return@use
                        if (DeviceHealth.tooHot(context)) error(context.getString(R.string.thermal_stop))
                        if (bytes != null) {
                            val offset = (frame % (bytes.size / stride)) * stride
                            bytes.copyInto(warped, 0, offset, offset + warped.size)
                            val buffer = ByteBuffer.wrap(bytes, offset + warped.size, packed.size * 4).order(ByteOrder.LITTLE_ENDIAN)
                            for (i in packed.indices) packed[i] = buffer.float
                        }
                        val start = System.nanoTime()
                        val output = engine.run(warped, packed, frame == 0)
                        AppState.stats.record((System.nanoTime() - start) / 1e6)
                        if (frame < recordedFrames) recorder.append(output)
                        if (frame + 1 == recordedFrames) recordingFile.outputStream().use { recorder.writeZip(it) }
                    }
                }
                AppState.phase = "idle"
                AppState.detail = if (AppState.benchmarkCancelled) "Benchmark stopped" else "100 frames completed; device acceptance pending"
            } catch (_: OutOfMemoryError) { AppState.fail(context.getString(R.string.memory_error)) }
              catch (e: Exception) { AppState.fail(e.message ?: e.javaClass.simpleName) }
            finally { AppState.busy = false; AppState.lastReport = AppState.report(context) }
        }, "jetlink-benchmark").start()
    }
}
