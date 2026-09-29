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
    @Volatile private var measurement = MeasurementRun(null, null, "none", "no run")
    val stats get() = measurement.stats
    @Volatile var benchmarkCancelled = false
    @Volatile var stopConnection: (() -> Unit)? = null
    @Volatile var lastReport: String? = null

    fun beginRun(context: Context, scope: String) {
        val model = ModelRepository.current(context)
        measurement = MeasurementRun(model?.artifactSha, model?.sourceSha, backend.name, scope)
    }

    fun fail(message: String) { phase = "error"; detail = message.take(500) }
    fun stop() {
        permissionGate.cancel(); connectionToken = -1
        benchmarkCancelled = true
        if (stopConnection != null) { phase = "stopping"; stopConnection?.invoke() }
        else if (phase == "permission" || phase == "connecting") { busy = false; phase = "idle" }
    }

    fun report(context: Context): String {
        return measurement.report().apply {
            put("app_version", BuildConfig.VERSION_NAME); put("device_model", Build.MODEL)
            put("android_release", Build.VERSION.RELEASE); put("api_level", Build.VERSION.SDK_INT)
            put("phase", phase); put("detail", detail)
            put("health_at_export", DeviceHealth.snapshot(context))
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
