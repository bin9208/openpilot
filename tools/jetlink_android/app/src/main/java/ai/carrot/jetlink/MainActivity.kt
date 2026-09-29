package ai.carrot.jetlink

import android.Manifest
import android.app.Activity
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.io.File

/** Small native UI; long-running model and USB work never holds an Activity reference. */
class MainActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var content: LinearLayout
    private lateinit var status: TextView
    private lateinit var model: TextView
    private lateinit var stats: TextView
    private lateinit var health: TextView
    private lateinit var diagnostics: TextView
    private val guarded = mutableListOf<View>()
    private val pages = mutableListOf<LinearLayout>()
    private var selected = 0
    private val refresh = object : Runnable {
        override fun run() { render(); handler.postDelayed(this, 500) }
    }
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    private fun text(parent: LinearLayout, value: String, size: Float = 17f): TextView = TextView(this).apply {
        text = value; textSize = size; setPadding(0, dp(10), 0, dp(10)); setTextColor(Color.rgb(224, 234, 234))
        parent.addView(this)
    }
    private fun button(parent: LinearLayout, label: Int, guard: Boolean = true, action: () -> Unit) = Button(this).apply {
        setText(label); isAllCaps = false; setOnClickListener { action() }; parent.addView(this)
        if (guard) guarded.add(this)
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        selected = savedInstanceState?.getInt("page") ?: 0
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(28), dp(24), dp(16)); setBackgroundColor(Color.rgb(12, 25, 30)) }
        setContentView(root)
        root.setOnApplyWindowInsetsListener { view, insets ->
            @Suppress("DEPRECATION")
            view.setPadding(dp(24) + insets.systemWindowInsetLeft, dp(16) + insets.systemWindowInsetTop,
                dp(24) + insets.systemWindowInsetRight, dp(16) + insets.systemWindowInsetBottom)
            insets
        }
        text(root, getString(R.string.app_name), 30f)
        text(root, getString(R.string.subtitle), 14f)
        val tabs = LinearLayout(this).apply { gravity = Gravity.CENTER; root.addView(this) }
        val scroll = ScrollView(this).apply { root.addView(this, LinearLayout.LayoutParams(-1, 0, 1f)) }
        content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; scroll.addView(this) }
        for ((index, title) in listOf(R.string.status, R.string.models, R.string.benchmark, R.string.logs).withIndex()) {
            val tab = Button(this).apply { setText(title); isAllCaps = false; setOnClickListener { selected = index; showPage() } }
            tabs.addView(tab, LinearLayout.LayoutParams(0, -2, 1f))
            pages.add(LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; content.addView(this) })
        }
        status = text(pages[0], "", 23f)
        text(pages[0], getString(R.string.device_format, Build.MODEL, Build.VERSION.RELEASE, Build.VERSION.SDK_INT))
        health = text(pages[0], "")
        button(pages[0], R.string.connect) { connect() }
        button(pages[0], R.string.stop, false) { AppState.stop() }
        text(pages[0], getString(R.string.acceptance_note), 14f)
        model = text(pages[1], "")
        text(pages[1], getString(R.string.model_help))
        button(pages[1], R.string.import_model) { startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).setType("application/zip").addCategory(Intent.CATEGORY_OPENABLE), IMPORT) }
        val providers = RadioGroup(this).apply { orientation = RadioGroup.VERTICAL; pages[1].addView(this) }
        for ((backend, label) in listOf(Backend.CPU to R.string.cpu, Backend.NNAPI to R.string.nnapi)) {
            val radio = RadioButton(this).apply { id = View.generateViewId(); setText(label); isChecked = AppState.backend == backend }
            providers.addView(radio); guarded.add(radio)
            radio.setOnClickListener { if (!AppState.busy) AppState.backend = backend }
        }
        text(pages[2], getString(R.string.benchmark_help))
        stats = text(pages[2], "", 21f)
        button(pages[2], R.string.run_benchmark) { Benchmark.start(applicationContext) }
        button(pages[2], R.string.stop, false) { AppState.stop() }
        button(pages[2], R.string.export_parity) {
            if (File(cacheDir, "parity.zip").isFile) startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT)
                .setType("application/zip").addCategory(Intent.CATEGORY_OPENABLE).putExtra(Intent.EXTRA_TITLE, "jetlink-parity.zip"), PARITY)
        }
        diagnostics = text(pages[3], "", 14f).apply { setTextIsSelectable(true) }
        button(pages[3], R.string.export_report, false) {
            AppState.lastReport = AppState.report(applicationContext)
            startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).setType("application/json").addCategory(Intent.CATEGORY_OPENABLE)
                .putExtra(Intent.EXTRA_TITLE, "jetlink-diagnostics.json"), EXPORT)
        }
        text(root, getString(R.string.not_driving), 12f)
        showPage()
    }
    private fun showPage() { pages.forEachIndexed { index, page -> page.visibility = if (index == selected) View.VISIBLE else View.GONE } }
    override fun onSaveInstanceState(outState: Bundle) { outState.putInt("page", selected); super.onSaveInstanceState(outState) }
    override fun onResume() { super.onResume(); handler.post(refresh) }
    override fun onPause() { handler.removeCallbacks(refresh); super.onPause() }
    private fun render() {
        val phase = when (AppState.phase) {
            "permission" -> R.string.permission; "connecting" -> R.string.connecting; "linked" -> R.string.linked
            "preparing" -> R.string.preparing; "benchmarking" -> R.string.benchmarking; "importing" -> R.string.importing
            "stopping" -> R.string.stopping; "error" -> R.string.error; else -> R.string.idle
        }
        status.text = getString(phase) + "\n" + AppState.detail
        val current = ModelRepository.current(this)
        model.text = current?.let { "${it.artifactSha}\n${it.artifactBytes / (1024 * 1024)} MiB · frame_skip ${it.frameSkip}" } ?: getString(R.string.no_model)
        val snapshot = AppState.stats.snapshot()
        stats.text = if (snapshot.frames == 0L) getString(R.string.no_frames) else getString(R.string.stats_format,
            snapshot.frames, snapshot.overBudget, snapshot.mean, snapshot.p50, snapshot.p95, snapshot.p99, snapshot.maximum)
        val state = DeviceHealth.snapshot(this)
        health.text = getString(R.string.health_format, state.getLong("available_memory_mib"), state.optString("battery_percent", "?"), state.getInt("thermal_status"))
        diagnostics.text = AppState.report(this)
        guarded.forEach { it.isEnabled = !AppState.busy }
        if (AppState.busy) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }
    private fun connect() {
        if (AppState.busy) return
        if (ModelRepository.current(this) == null) { AppState.fail(getString(R.string.no_model)); return }
        val manager = getSystemService(UsbManager::class.java)
        val device = manager.deviceList.values.firstOrNull { UsbLink.supported(it) }
        if (device == null) { AppState.fail(getString(R.string.no_usb)); return }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 3)
        val token = AppState.permissionGate.begin(device.deviceId)
        AppState.connectionToken = token; AppState.deviceName = device.deviceName
        AppState.busy = true; AppState.phase = "permission"; AppState.detail = ""
        val intent = Intent(this, UsbPermissionReceiver::class.java).setAction(UsbPermissionReceiver.ACTION).putExtra("token", token)
        // USB framework supplies the device/result extras; explicit receiver keeps the mutable intent scoped.
        val flags = PendingIntent.FLAG_CANCEL_CURRENT or if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
        try { manager.requestPermission(device, PendingIntent.getBroadcast(this, token.toInt(), intent, flags)) }
        catch (e: Exception) { AppState.stop(); AppState.fail(e.message ?: getString(R.string.usb_denied)) }
    }
    @Deprecated("Platform activity result API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val uri = data?.data ?: return
        if (resultCode != RESULT_OK) return
        val context = applicationContext
        if (requestCode == IMPORT && !AppState.busy) {
            AppState.busy = true; AppState.phase = "importing"; AppState.detail = ""
            Thread({
                try {
                    val imported = context.contentResolver.openInputStream(uri)!!.use { ModelArchive.importZip(it, ModelRepository.root(context)) }
                    ModelRepository.select(context, imported); AppState.phase = "idle"; AppState.detail = context.getString(R.string.model_imported)
                } catch (_: OutOfMemoryError) { AppState.fail(context.getString(R.string.memory_error)) }
                  catch (e: Exception) { AppState.fail(e.message ?: e.javaClass.simpleName) }
                finally { AppState.busy = false }
            }, "jetlink-import").start()
        } else if (requestCode == EXPORT) {
            val report = AppState.lastReport ?: AppState.report(context)
            Thread({
                try {
                    context.contentResolver.openOutputStream(uri, "wt")!!.use { it.write(report.toByteArray(Charsets.UTF_8)) }
                    Handler(Looper.getMainLooper()).post { Toast.makeText(context, R.string.report_saved, Toast.LENGTH_SHORT).show() }
                } catch (e: Exception) { AppState.fail(e.message ?: e.javaClass.simpleName) }
            }, "jetlink-export").start()
        } else if (requestCode == PARITY && !AppState.busy) {
            Thread({
                try { context.contentResolver.openOutputStream(uri, "wt")!!.use { output -> File(context.cacheDir, "parity.zip").inputStream().use { it.copyTo(output) } } }
                catch (e: Exception) { AppState.fail(e.message ?: e.javaClass.simpleName) }
            }, "jetlink-parity-export").start()
        }
    }
    companion object { private const val IMPORT = 1; private const val EXPORT = 2; private const val PARITY = 4 }
}
