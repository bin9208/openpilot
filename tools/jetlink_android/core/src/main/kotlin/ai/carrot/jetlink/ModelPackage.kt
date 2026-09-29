package ai.carrot.jetlink

import java.io.File
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject

data class ModelTensor(val dtype: String, val shape: LongArray, val elements: Int, val bytes: Int)

class ModelPackage private constructor(
    val root: File, val manifestText: String, val sourceSha: String, val artifactSha: String,
    val artifactBytes: Long, val checkpoint: String, val frameSkip: Int,
    val inputs: Map<String, ModelTensor>, val outputs: Map<String, ModelTensor>,
    val outputSlices: Map<String, IntRange>, val statePairs: Map<String, String>, val modelFile: File,
) {
    val stateful = inputs.containsKey("new_img")
    private val imageShape = inputs[if (stateful) "new_img" else "img"]!!.shape
    val warpedBytes = Math.toIntExact(Math.multiplyExact(Math.multiplyExact(imageShape[2], imageShape[3]), 12L))
    val packedCount = if (stateful) inputs.getValue("desire").elements + inputs.getValue("traffic_convention").elements + inputs.getValue("action_t").elements
        else inputs.getValue("desire_pulse").shape[2].toInt() + inputs.getValue("traffic_convention").elements + inputs.getValue("action_t").elements +
            inputs.getValue("features_buffer").shape.drop(2).fold(1L, Math::multiplyExact).toInt()
    val outputCount = outputs.getValue("outputs").elements

    fun wireSpec(): JSONObject = JSONObject().apply {
        put("sha256", sourceSha); put("nbytes", JSONObject(manifestText).getJSONObject("source").getLong("bytes"))
        put("frame_skip", frameSkip); put("checkpoint", checkpoint)
        put("input_shapes", JSONObject().apply { inputs.forEach { (name, tensor) -> put(name, JSONArray(tensor.shape.toList())) } })
        put("output_shapes", JSONObject().apply { outputs.forEach { (name, tensor) -> put(name, JSONArray(tensor.shape.toList())) } })
        put("output_slices", JSONObject().apply { outputSlices.forEach { (name, range) -> put(name, JSONArray(listOf(range.first, range.last + 1))) } })
    }

    companion object {
        const val MAX_MODEL_BYTES = 4L * 1024 * 1024 * 1024
        const val MAX_MANIFEST_BYTES = 64 * 1024
        private val widths = mapOf("uint8" to 1, "bool" to 1, "float16" to 2, "float32" to 4, "float64" to 8, "int32" to 4, "int64" to 8)
        private val digestPattern = Regex("[0-9a-f]{64}")

        fun hash(file: File): String {
            val digest = MessageDigest.getInstance("SHA-256")
            file.inputStream().use { stream ->
                val buffer = ByteArray(64 * 1024)
                while (true) { val count = stream.read(buffer); if (count < 0) break; digest.update(buffer, 0, count) }
            }
            return digest.digest().joinToString("") { "%02x".format(it) }
        }

        private fun integer(value: Any, low: Long = 1, high: Long = MAX_MODEL_BYTES): Long {
            require(value is Int || value is Long) { "Expected integer model metadata" }
            return (value as Number).toLong().also { require(it in low..high) { "Model integer outside limits" } }
        }

        private fun tensors(json: JSONObject): Map<String, ModelTensor> {
            require(json.length() in 1..128)
            val result = linkedMapOf<String, ModelTensor>()
            for (name in json.keys()) {
                require(name.isNotEmpty() && name.length <= 256)
                val obj = json.getJSONObject(name)
                val type = obj.getString("dtype")
                val width = widths[type] ?: error("Unsupported tensor type: $type")
                val dimensions = obj.getJSONArray("shape")
                require(dimensions.length() in 1..8)
                val shape = LongArray(dimensions.length()) { integer(dimensions.get(it), high = Int.MAX_VALUE.toLong()) }
                val elements = shape.fold(1L, Math::multiplyExact)
                val bytes = Math.multiplyExact(elements, width.toLong())
                require(bytes <= 256L * 1024 * 1024) { "Tensor exceeds Android memory limit" }
                result[name] = ModelTensor(type, shape, elements.toInt(), bytes.toInt())
            }
            return result
        }

        fun parse(text: String, root: File, verifyArtifact: Boolean = true): ModelPackage {
            try {
                require(text.toByteArray(Charsets.UTF_8).size <= MAX_MANIFEST_BYTES)
                val json = JSONObject(text)
                require(integer(json.get("schema_version"), 1, 1) == 1L)
                require(integer(json.get("prepare_version"), 1, 1) == 1L)
                val skip = integer(json.get("frame_skip"), 1, 4).toInt()
                require(skip in listOf(1, 2, 4))
                val source = json.getJSONObject("source")
                val artifact = json.getJSONObject("artifact")
                val sourceSha = source.getString("sha256")
                val artifactSha = artifact.getString("sha256")
                require(digestPattern.matches(sourceSha) && digestPattern.matches(artifactSha))
                integer(source.get("bytes"))
                val size = integer(artifact.get("bytes"))
                val checkpoint = source.getString("checkpoint")
                require(checkpoint.isNotBlank() && checkpoint.length <= 256)
                val runtime = json.getJSONObject("runtime")
                require(runtime.getString("name") == "onnxruntime")
                require(runtime.getString("backend") in listOf("cpu", "nnapi", "qnn"))
                require(runtime.getString("version").isNotBlank())
                val name = artifact.getString("path")
                require(name == "model.onnx") { "Android packages require model.onnx" }
                require(name.isNotEmpty() && !File(name).isAbsolute && ':' !in name && '\\' !in name)
                require(name.split('/').none { it.isEmpty() || it == "." || it == ".." })
                val file = File(root, name).canonicalFile
                require(file.toPath().startsWith(root.canonicalFile.toPath())) { "Model path escapes package" }
                val inputs = tensors(json.getJSONObject("inputs"))
                val outputs = tensors(json.getJSONObject("outputs"))
                val image = inputs[if (inputs.containsKey("new_img")) "new_img" else "img"] ?: error("No driving images")
                require(image.shape.size == 4 && inputs.containsKey("traffic_convention") && inputs.containsKey("action_t"))
                if (inputs.containsKey("new_img")) require(image.shape[0] == 2L && image.shape[1] == 6L && inputs.containsKey("desire"))
                else {
                    require(inputs.containsKey("big_img") && inputs.containsKey("desire_pulse") && inputs.containsKey("features_buffer"))
                    require(image.shape[0] == 1L && image.shape[1] >= 12 && image.shape[1] % 6L == 0L)
                    require(image.shape.contentEquals(inputs.getValue("big_img").shape))
                    require(inputs.getValue("desire_pulse").shape.size == 3 && inputs.getValue("features_buffer").shape.size >= 3)
                }
                val driving = outputs.getValue("outputs")
                require(driving.dtype in listOf("float16", "float32"))
                val pairs = linkedMapOf<String, String>()
                val pairJson = json.getJSONObject("state_pairs")
                for (key in pairJson.keys()) pairs[key] = pairJson.getString(key)
                val expected = inputs.keys.filter { it.startsWith("state_") }.associateWith { "next_$it" }
                require(pairs == expected && (!inputs.containsKey("new_img") || pairs.isNotEmpty()))
                for ((input, output) in pairs) {
                    require(inputs.getValue(input).dtype == outputs.getValue(output).dtype &&
                        inputs.getValue(input).shape.contentEquals(outputs.getValue(output).shape)) { "State I/O mismatch" }
                }
                val slices = linkedMapOf<String, IntRange>()
                val sliceJson = json.getJSONObject("output_slices")
                require(sliceJson.length() > 0)
                for (key in sliceJson.keys()) {
                    val bounds = sliceJson.getJSONArray(key)
                    require(bounds.length() == 2)
                    val start = integer(bounds.get(0), 0, driving.elements.toLong()).toInt()
                    val stop = integer(bounds.get(1), 1, driving.elements.toLong()).toInt()
                    require(start < stop)
                    slices[key] = start until stop
                }
                val ordered = slices.values.sortedBy { it.first }
                require(ordered.zipWithNext().all { (a, b) -> a.last < b.first }) { "Overlapping output slices" }
                if (verifyArtifact) require(file.isFile && file.length() == size && hash(file) == artifactSha) { "Model size or SHA-256 mismatch" }
                return ModelPackage(root, text, sourceSha, artifactSha, size, checkpoint, skip, inputs, outputs, slices, pairs, file).also {
                    require(it.warpedBytes.toLong() + it.packedCount.toLong() * 4 + 8 <= Wire.MAX_PAYLOAD)
                }
            } catch (e: Exception) {
                throw IllegalArgumentException(e.message ?: "Invalid model package", e)
            }
        }
    }
}
