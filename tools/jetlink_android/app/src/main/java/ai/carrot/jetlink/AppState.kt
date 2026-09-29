package ai.carrot.jetlink

import android.app.ActivityManager
import android.content.Context
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import java.io.File
import org.json.JSONObject

object AppState {
    val permissionGate = PermissionGate()
    @Volatile var connectionToken = -1L
    @Volatile var phase = "idle"
    @Volatile var detail = ""
    @Volatile var busy = false
    @Volatile var deviceName: String? = null
    @Volatile var backend = Backend.CPU
    @Volatile var stats = FrameStats()
    @Volatile var benchmarkCancelled = false
    @Volatile var stopConnection: (() -> Unit)? = null
    @Volatile var lastReport: String? = null

    fun fail(message: String) { phase = "error"; detail = message.take(500) }
    fun stop() {
        permissionGate.cancel(); connectionToken = -1
        benchmarkCancelled = true
        if (stopConnection != null) { phase = "stopping"; stopConnection?.invoke() }
        else if (phase == "permission" || phase == "connecting") { busy = false; phase = "idle" }
    }

    fun report(context: Context): String {
        val model = ModelRepository.current(context)
        val s = stats.snapshot()
        return JSONObject().apply {
            put("app_version", "0.1.0-experimental"); put("device_model", Build.MODEL)
            put("android_release", Build.VERSION.RELEASE); put("api_level", Build.VERSION.SDK_INT)
            put("backend_requested", backend.name); put("provider_partitioning_verified", false)
            put("phase", phase); put("detail", detail)
            put("model_sha256", model?.artifactSha ?: JSONObject.NULL)
            put("source_sha256", model?.sourceSha ?: JSONObject.NULL)
            put("health", DeviceHealth.snapshot(context))
            put("frames", s.frames); put("over_50ms", s.overBudget)
            put("mean_ms", s.mean); put("p50_ms", s.p50); put("p95_ms", s.p95); put("p99_ms", s.p99); put("max_ms", s.maximum)
            put("percentile_scope", "last 2000 app samples; mean/max/misses cover the whole run")
            put("scope", "App inference or app processing/send time; excludes C3X warp and full link latency. Not vehicle acceptance.")
        }.toString(2)
    }
}

object ModelRepository {
    fun root(context: Context) = File(context.filesDir, "models").apply { mkdirs() }
    fun select(context: Context, model: ModelPackage) {
        context.getSharedPreferences("jetlink", Context.MODE_PRIVATE).edit().putString("model", model.root.name).apply()
    }
    fun current(context: Context, verify: Boolean = false): ModelPackage? {
        val key = context.getSharedPreferences("jetlink", Context.MODE_PRIVATE).getString("model", null) ?: return null
        if (!Regex("[0-9a-f]{64}-[124]").matches(key)) return null
        return try {
            val folder = File(root(context), key)
            val manifest = File(folder, "manifest.json")
            if (!manifest.isFile || manifest.length() > ModelPackage.MAX_MANIFEST_BYTES) null
            else ModelPackage.parse(manifest.readText(Charsets.UTF_8), folder, verify)
        } catch (_: Exception) { null }
    }
}

object DeviceHealth {
    fun thermal(context: Context): Int = if (Build.VERSION.SDK_INT >= 29)
        context.getSystemService(PowerManager::class.java).currentThermalStatus else -1
    fun tooHot(context: Context) = thermal(context) >= 3
    fun snapshot(context: Context): JSONObject {
        val memory = ActivityManager.MemoryInfo()
        context.getSystemService(ActivityManager::class.java).getMemoryInfo(memory)
        val capacity = context.getSystemService(BatteryManager::class.java).getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        return JSONObject().apply {
            put("available_memory_mib", memory.availMem / (1024 * 1024)); put("total_memory_mib", memory.totalMem / (1024 * 1024))
            put("battery_percent", if (capacity in 0..100) capacity else JSONObject.NULL)
            put("thermal_status", thermal(context))
        }
    }
}
