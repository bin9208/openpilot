package ai.comma.galaxyhud;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.widget.EditText;

public final class MainActivity extends Activity implements HudClient.Listener {
  private static final String PREFS = "hud";
  private static final String KEY_ENDPOINT = "endpoint";
  private static final String DEFAULT_ENDPOINT = "127.0.0.1:28888";

  private HudView hudView;
  private HudClient client;
  private SharedPreferences prefs;

  @Override
  protected void onCreate(Bundle savedInstanceState) {
    super.onCreate(savedInstanceState);
    requestWindowFeature(Window.FEATURE_NO_TITLE);
    getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON | WindowManager.LayoutParams.FLAG_FULLSCREEN);
    setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);

    prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    hudView = new HudView(this);
    hudView.setSettingsListener(this::showConnectionDialog);
    setContentView(hudView);
    startClient();
  }

  @Override
  protected void onStart() {
    super.onStart();
    if (client != null) {
      client.start();
    }
  }

  @Override
  protected void onStop() {
    if (client != null) {
      client.stop();
    }
    super.onStop();
  }

  @Override
  protected void onResume() {
    super.onResume();
    hideSystemUi();
  }

  @Override
  public void onWindowFocusChanged(boolean hasFocus) {
    super.onWindowFocusChanged(hasFocus);
    if (hasFocus) {
      hideSystemUi();
    }
  }

  @Override
  public void onTelemetry(HudTelemetry telemetry) {
    runOnUiThread(() -> hudView.setTelemetry(telemetry));
  }

  @Override
  public void onConnectionState(String state, boolean connected) {
    runOnUiThread(() -> hudView.setConnectionState(state, connected));
  }

  private void startClient() {
    String endpoint = prefs.getString(KEY_ENDPOINT, DEFAULT_ENDPOINT);
    if (client != null) {
      client.stop();
    }
    client = new HudClient(this, endpoint);
    client.start();
  }

  private void showConnectionDialog() {
    hideSystemUi();
    EditText input = new EditText(this);
    input.setSingleLine(true);
    input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
    input.setText(prefs.getString(KEY_ENDPOINT, DEFAULT_ENDPOINT));
    input.setSelectAllOnFocus(true);

    new AlertDialog.Builder(this)
        .setTitle("HUD endpoint")
        .setView(input)
        .setPositiveButton("Connect", (dialog, which) -> {
          String value = input.getText().toString().trim();
          if (!value.isEmpty()) {
            prefs.edit().putString(KEY_ENDPOINT, value).apply();
            startClient();
          }
          hideSystemUi();
        })
        .setNegativeButton("Cancel", (dialog, which) -> hideSystemUi())
        .show();
  }

  private void hideSystemUi() {
    getWindow().getDecorView().setSystemUiVisibility(
        View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            | View.SYSTEM_UI_FLAG_FULLSCREEN
            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
  }
}
