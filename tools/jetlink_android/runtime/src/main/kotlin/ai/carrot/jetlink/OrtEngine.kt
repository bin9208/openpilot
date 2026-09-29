package ai.carrot.jetlink

import ai.onnxruntime.NodeInfo
import ai.onnxruntime.OnnxJavaType
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import ai.onnxruntime.TensorInfo
import ai.onnxruntime.platform.Fp16Conversions
import ai.onnxruntime.providers.NNAPIFlags
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.EnumSet

enum class Backend { CPU, NNAPI }

/** Same Java API on desktop tests and Android; Android supplies its own native ORT AAR. */
class OrtEngine(private val backend: Backend = Backend.CPU) : InferenceEngine {
    private val environment = OrtEnvironment.getEnvironment()
    private var session: OrtSession? = null
    private var model: ModelPackage? = null
    private val buffers = linkedMapOf<String, ByteBuffer>()
    private val tensors = linkedMapOf<String, OnnxTensor>()

    private fun type(dtype: String): OnnxJavaType = when (dtype) {
        "uint8" -> OnnxJavaType.UINT8
        "float16" -> OnnxJavaType.FLOAT16
        "float32" -> OnnxJavaType.FLOAT
        "float64" -> OnnxJavaType.DOUBLE
        "int32" -> OnnxJavaType.INT32
        "int64" -> OnnxJavaType.INT64
        "bool" -> OnnxJavaType.BOOL
        else -> throw IllegalArgumentException("Unsupported tensor dtype")
    }

    private fun checkIo(expected: Map<String, ModelTensor>, actual: Map<String, NodeInfo>) {
        require(expected.keys == actual.keys) { "Runtime tensor names do not match manifest" }
        for ((name, tensor) in expected) {
            val info = actual.getValue(name).info as? TensorInfo ?: error("Non-tensor model I/O")
            require(tensor.shape.contentEquals(info.shape) && type(tensor.dtype) == info.type) { "Runtime tensor $name differs from manifest" }
        }
    }

    @Synchronized override fun prepare(model: ModelPackage) {
        close()
        val verified = ModelPackage.parse(model.manifestText, model.root)
        require(verified.stateful) { "Queued models are not enabled: numerical conformance is unverified" }
        try {
            OrtSession.SessionOptions().use { options ->
                options.setIntraOpNumThreads(4)
                options.setInterOpNumThreads(1)
                options.setSessionLogLevel(ai.onnxruntime.OrtLoggingLevel.ORT_LOGGING_LEVEL_ERROR)
                if (backend == Backend.NNAPI) options.addNnapi(EnumSet.of(NNAPIFlags.CPU_DISABLED))
                session = environment.createSession(verified.modelFile.absolutePath, options)
            }
            checkIo(verified.inputs, session!!.inputInfo)
            checkIo(verified.outputs, session!!.outputInfo)
            this.model = verified
            for ((name, info) in verified.inputs) {
                val buffer = ByteBuffer.allocateDirect(info.bytes).order(ByteOrder.nativeOrder())
                buffers[name] = buffer
                tensors[name] = OnnxTensor.createTensor(environment, buffer, info.shape, type(info.dtype))
            }
            val warm = FloatArray(verified.packedCount)
            val desire = verified.inputs.getValue("desire").elements
            val traffic = verified.inputs.getValue("traffic_convention").elements
            if (traffic == 2) warm[desire + 1] = 1f
            val action = desire + traffic
            if (verified.inputs.getValue("action_t").elements == 2) { warm[action] = 0.1f; warm[action + 1] = 0.3f }
            repeat(2) { run(ByteArray(verified.warpedBytes), warm, true) }
            resetState()
        } catch (e: OutOfMemoryError) { close(); throw e }
          catch (e: Exception) { close(); throw e }
    }

    private fun resetState() {
        for (name in model!!.statePairs.keys) {
            val buffer = buffers.getValue(name)
            buffer.clear()
            while (buffer.remaining() >= 8) buffer.putLong(0)
            while (buffer.hasRemaining()) buffer.put(0)
            buffer.rewind()
        }
    }

    private fun putFloat(buffer: ByteBuffer, dtype: String, value: Float) {
        require(value.isFinite()) { "Nonfinite model input" }
        when (dtype) {
            "float32" -> buffer.putFloat(value)
            "float16" -> {
                require(value in -65504f..65504f) { "Input overflows fp16" }
                buffer.putShort(Fp16Conversions.floatToFp16(value))
            }
            else -> throw IllegalArgumentException("Scalar input must be float32 or float16")
        }
    }

    @Synchronized override fun run(warped: ByteArray, packed: FloatArray, reset: Boolean): FloatArray {
        val model = checkNotNull(model) { "Model is not prepared" }
        val session = checkNotNull(session)
        require(warped.size == model.warpedBytes && packed.size == model.packedCount)
        require(packed.all { it.isFinite() })
        if (reset) resetState()
        val image = buffers.getValue("new_img")
        image.clear()
        val imageType = model.inputs.getValue("new_img").dtype
        if (imageType == "uint8") image.put(warped)
        else warped.forEach { putFloat(image, imageType, (it.toInt() and 255).toFloat()) }
        image.rewind()
        var offset = 0
        for (name in listOf("desire", "traffic_convention", "action_t")) {
            val info = model.inputs.getValue(name)
            val buffer = buffers.getValue(name)
            buffer.clear()
            repeat(info.elements) { putFloat(buffer, info.dtype, packed[offset++]) }
            buffer.rewind()
        }
        session.run(tensors).use { result ->
            // Validate every floating output/state before mutating recurrent buffers.
            for ((name, info) in model.outputs) {
                val value = result.get(name).orElseThrow() as OnnxTensor
                if (info.dtype in listOf("float16", "float32")) {
                    val floats = value.floatBuffer
                    while (floats.hasRemaining()) require(floats.get().isFinite()) { "Nonfinite model output/state" }
                } else if (info.dtype == "float64") {
                    val doubles = value.doubleBuffer
                    while (doubles.hasRemaining()) require(doubles.get().isFinite()) { "Nonfinite model state" }
                }
            }
            val driving = (result.get("outputs").orElseThrow() as OnnxTensor).floatBuffer
            val values = FloatArray(model.outputCount)
            require(driving.remaining() == values.size)
            driving.get(values)
            for ((input, output) in model.statePairs) {
                val value = (result.get(output).orElseThrow() as OnnxTensor).byteBuffer
                val buffer = buffers.getValue(input)
                require(value.remaining() == buffer.capacity())
                buffer.clear(); buffer.put(value); buffer.rewind()
            }
            return values
        }
    }

    @Synchronized override fun close() {
        tensors.values.forEach { it.close() }
        tensors.clear(); buffers.clear()
        session?.close(); session = null; model = null
    }
}
