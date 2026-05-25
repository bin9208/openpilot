package ai.comma.galaxyhud;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PointF;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.view.MotionEvent;
import android.view.View;

final class HudView extends View {
  interface SettingsListener {
    void onSettingsRequested();
  }

  private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  private final Paint mono = new Paint(Paint.ANTI_ALIAS_FLAG);
  private final RectF rect = new RectF();
  private final PointF tmpPoint = new PointF();
  private HudTelemetry telemetry;
  private String connectionState = "WAITING";
  private boolean connected;
  private SettingsListener settingsListener;
  private long downAt;

  HudView(Context context) {
    super(context);
    paint.setTypeface(Typeface.create(Typeface.SANS_SERIF, Typeface.NORMAL));
    mono.setTypeface(Typeface.create(Typeface.MONOSPACE, Typeface.BOLD));
    setBackgroundColor(Color.rgb(2, 7, 19));
    setFocusable(true);
  }

  void setSettingsListener(SettingsListener settingsListener) {
    this.settingsListener = settingsListener;
  }

  void setTelemetry(HudTelemetry telemetry) {
    this.telemetry = telemetry;
    invalidate();
  }

  void setConnectionState(String state, boolean connected) {
    this.connectionState = state == null ? "" : state;
    this.connected = connected;
    invalidate();
  }

  @Override
  public boolean onTouchEvent(MotionEvent event) {
    if (event.getAction() == MotionEvent.ACTION_DOWN) {
      downAt = System.currentTimeMillis();
      return true;
    }
    if (event.getAction() == MotionEvent.ACTION_UP) {
      if (System.currentTimeMillis() - downAt > 650 && settingsListener != null) {
        settingsListener.onSettingsRequested();
      }
      return true;
    }
    return true;
  }

  @Override
  protected void onDraw(Canvas canvas) {
    super.onDraw(canvas);
    long now = System.currentTimeMillis();
    HudTelemetry t = telemetry == null ? HudTelemetry.demo(now) : telemetry;

    int w = getWidth();
    int h = getHeight();
    drawBackground(canvas, w, h);

    float leftW = w * 0.285f;
    float rightW = w * 0.255f;
    RectF left = new RectF(0, 0, leftW, h);
    RectF center = new RectF(leftW, 0, w - rightW, h);
    RectF right = new RectF(w - rightW, 0, w, h);

    drawRoad(canvas, center, t, now);
    drawLeftPanel(canvas, left, t);
    drawTop(canvas, center, t, now);
    drawRightPanel(canvas, right, t, now);
    drawAlerts(canvas, w, h, t);
    drawStatus(canvas, w, h);

    postInvalidateOnAnimation();
  }

  private void drawBackground(Canvas canvas, int w, int h) {
    LinearGradient gradient = new LinearGradient(0, 0, 0, h, Color.rgb(5, 11, 29), Color.rgb(1, 4, 12), Shader.TileMode.CLAMP);
    paint.setShader(gradient);
    canvas.drawRect(0, 0, w, h, paint);
    paint.setShader(null);
  }

  private void drawLeftPanel(Canvas canvas, RectF area, HudTelemetry t) {
    float pad = area.width() * 0.11f;
    float gaugeW = area.width() * 0.10f;
    float gaugeL = pad;
    float top = area.height() * 0.21f;
    float bottom = area.height() * 0.72f;

    mono.setTextAlign(Paint.Align.LEFT);
    mono.setFakeBoldText(true);
    mono.setColor(Color.rgb(210, 230, 255));
    fitText(mono, "SET " + whole(t.setSpeedKph), area.width() - pad * 1.8f, area.height() * 0.060f, 20f);
    canvas.drawText("SET " + whole(t.setSpeedKph), pad * 2.25f, area.height() * 0.15f, mono);

    mono.setColor(Color.WHITE);
    fitText(mono, whole(t.speedKph), area.width() - pad * 2.4f, area.height() * 0.185f, 62f);
    canvas.drawText(whole(t.speedKph), pad * 2.08f, area.height() * 0.43f, mono);

    float accel = clamp(t.accel, -4f, 4f);
    int accelColor = accel >= 0 ? Color.rgb(43, 242, 203) : Color.rgb(255, 58, 111);
    mono.setColor(accelColor);
    fitText(mono, signed(t.accel), area.width() * 0.25f, area.height() * 0.045f, 18f);
    canvas.drawText(signed(t.accel), pad * 0.35f, area.height() * 0.105f, mono);

    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(2.6f);
    paint.setColor(Color.argb(170, 93, 137, 255));
    rect.set(gaugeL, top, gaugeL + gaugeW, bottom);
    canvas.drawRoundRect(rect, gaugeW * 0.45f, gaugeW * 0.45f, paint);

    paint.setStyle(Paint.Style.FILL);
    paint.setColor(Color.argb(232, Color.red(accelColor), Color.green(accelColor), Color.blue(accelColor)));
    float mid = (top + bottom) * 0.5f;
    float range = (bottom - top) * 0.46f;
    float y = mid - (accel / 4f) * range;
    rect.set(gaugeL + gaugeW * 0.17f, Math.min(mid, y) - 2f, gaugeL + gaugeW * 0.83f, Math.max(mid, y) + 2f);
    canvas.drawRoundRect(rect, gaugeW * 0.25f, gaugeW * 0.25f, paint);

    mono.setColor(Color.rgb(154, 190, 255));
    fitText(mono, "m/s^2", area.width() * 0.20f, area.height() * 0.025f, 12f);
    canvas.drawText("m/s^2", gaugeL - gaugeW * 0.05f, bottom + area.height() * 0.045f, mono);
  }

  private void drawTop(Canvas canvas, RectF area, HudTelemetry t, long now) {
    float top = area.height() * 0.065f;
    float icon = area.height() * 0.075f;
    drawArrow(canvas, area.left + area.width() * 0.18f, top, icon, true, t.leftBlinker, now);
    drawArrow(canvas, area.right - area.width() * 0.18f, top, icon, false, t.rightBlinker, now);

    float circleR = area.height() * 0.047f;
    float cx = area.centerX();
    float cy = top + icon * 0.35f;
    paint.setStyle(Paint.Style.FILL);
    paint.setColor(Color.WHITE);
    canvas.drawCircle(cx, cy, circleR, paint);
    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(circleR * 0.18f);
    paint.setColor(Color.rgb(255, 62, 104));
    canvas.drawCircle(cx, cy, circleR * 0.88f, paint);
    mono.setTextAlign(Paint.Align.CENTER);
    mono.setColor(Color.rgb(39, 49, 68));
    fitText(mono, whole(t.speedLimitKph > 0 ? t.speedLimitKph : 100), circleR * 1.35f, circleR * 0.70f, 16f);
    canvas.drawText(whole(t.speedLimitKph > 0 ? t.speedLimitKph : 100), cx, cy + mono.getTextSize() * 0.35f, mono);
  }

  private void drawArrow(Canvas canvas, float cx, float y, float size, boolean left, boolean active, long now) {
    boolean lit = active && ((now / 420) % 2 == 0);
    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(size * 0.075f);
    paint.setStrokeJoin(Paint.Join.ROUND);
    paint.setColor(lit ? Color.rgb(46, 228, 255) : Color.argb(180, 84, 139, 255));
    Path path = new Path();
    float dir = left ? -1f : 1f;
    path.moveTo(cx + dir * size * 0.42f, y);
    path.lineTo(cx - dir * size * 0.04f, y);
    path.lineTo(cx - dir * size * 0.04f, y - size * 0.22f);
    path.lineTo(cx - dir * size * 0.48f, y + size * 0.20f);
    path.lineTo(cx - dir * size * 0.04f, y + size * 0.62f);
    path.lineTo(cx - dir * size * 0.04f, y + size * 0.40f);
    path.lineTo(cx + dir * size * 0.42f, y + size * 0.40f);
    canvas.drawPath(path, paint);
  }

  private void drawRoad(Canvas canvas, RectF area, HudTelemetry t, long now) {
    float horizon = area.top + area.height() * 0.245f;
    float bottom = area.bottom - area.height() * 0.070f;

    paint.setStyle(Paint.Style.FILL);
    LinearGradient roadGradient = new LinearGradient(area.left, horizon, area.left, bottom, Color.rgb(6, 13, 31), Color.rgb(2, 6, 16), Shader.TileMode.CLAMP);
    paint.setShader(roadGradient);
    canvas.drawRect(area, paint);
    paint.setShader(null);

    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(2f);
    paint.setColor(Color.argb(130, 78, 115, 210));
    canvas.drawLine(area.centerX(), horizon, area.left + area.width() * 0.06f, bottom, paint);
    canvas.drawLine(area.centerX(), horizon, area.right - area.width() * 0.06f, bottom, paint);

    for (int i = 0; i < t.roadEdges.size(); i++) {
      drawModelLine(canvas, area, horizon, bottom, t.roadEdges.get(i), Color.argb(150, 255, 86, 77), 2.0f);
    }

    if (t.path != null) {
      drawModelLine(canvas, area, horizon, bottom, t.path, Color.argb(210, 37, 155, 255), area.height() * 0.013f);
      drawModelLine(canvas, area, horizon, bottom, offsetLine(t.path, -0.45f), Color.argb(220, 18, 225, 255), 2.6f);
      drawModelLine(canvas, area, horizon, bottom, offsetLine(t.path, 0.45f), Color.argb(220, 18, 225, 255), 2.6f);
    }

    for (int i = 0; i < t.laneLines.size(); i++) {
      boolean yellow = (i == 1 && t.leftLaneLine >= 20) || (i == 2 && t.rightLaneLine >= 20);
      int color = yellow ? Color.argb(220, 255, 215, 72) : Color.argb(220, 46, 170, 255);
      drawModelLine(canvas, area, horizon, bottom, t.laneLines.get(i), color, 3.0f);
    }

    for (HudTelemetry.Lead lead : t.leads) {
      drawLead(canvas, area, horizon, bottom, lead);
    }
    for (HudTelemetry.RadarPoint point : t.radarPoints) {
      drawRadarPoint(canvas, area, horizon, bottom, point);
    }
    drawSelfCar(canvas, area, bottom, t.active);
  }

  private void drawModelLine(Canvas canvas, RectF area, float horizon, float bottom, HudTelemetry.Line line, int color, float stroke) {
    if (line == null || line.size() < 2) {
      return;
    }
    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeCap(Paint.Cap.ROUND);
    paint.setStrokeJoin(Paint.Join.ROUND);
    paint.setStrokeWidth(stroke);
    paint.setColor(color);
    Path path = new Path();
    for (int i = 0; i < line.size(); i++) {
      project(area, horizon, bottom, line.x[i], line.y[i], tmpPoint);
      if (i == 0) {
        path.moveTo(tmpPoint.x, tmpPoint.y);
      } else {
        path.lineTo(tmpPoint.x, tmpPoint.y);
      }
    }
    canvas.drawPath(path, paint);
  }

  private HudTelemetry.Line offsetLine(HudTelemetry.Line source, float offset) {
    float[] x = new float[source.x.length];
    float[] y = new float[source.y.length];
    System.arraycopy(source.x, 0, x, 0, x.length);
    for (int i = 0; i < y.length; i++) {
      y[i] = source.y[i] + offset;
    }
    return new HudTelemetry.Line(x, y);
  }

  private void drawLead(Canvas canvas, RectF area, float horizon, float bottom, HudTelemetry.Lead lead) {
    if (!lead.status || lead.dRel < 1f) {
      return;
    }
    project(area, horizon, bottom, lead.dRel, -lead.yRel, tmpPoint);
    float depth = clamp(lead.dRel / 100f, 0f, 1f);
    float width = area.width() * (0.075f * (1f - depth) + 0.020f);
    float height = width * 0.52f;
    int fill = lead.fcw ? Color.argb(230, 255, 55, 77) : Color.argb(180, 38, 164, 255);
    paint.setStyle(Paint.Style.FILL);
    paint.setColor(fill);
    rect.set(tmpPoint.x - width * 0.5f, tmpPoint.y - height, tmpPoint.x + width * 0.5f, tmpPoint.y);
    canvas.drawRoundRect(rect, height * 0.16f, height * 0.16f, paint);
    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(2.0f);
    paint.setColor(Color.argb(220, 160, 220, 255));
    canvas.drawRoundRect(rect, height * 0.16f, height * 0.16f, paint);

    mono.setTextAlign(Paint.Align.CENTER);
    mono.setColor(Color.WHITE);
    fitText(mono, whole(lead.dRel) + " m", width * 1.6f, 18f, 10f);
    canvas.drawText(whole(lead.dRel) + " m", tmpPoint.x, rect.top - 8f, mono);
    fitText(mono, whole(lead.vRel * 3.6f) + " km/h", width * 1.7f, 15f, 9f);
    canvas.drawText(whole(lead.vRel * 3.6f) + " km/h", tmpPoint.x, rect.top - 26f, mono);
  }

  private void drawRadarPoint(Canvas canvas, RectF area, float horizon, float bottom, HudTelemetry.RadarPoint point) {
    project(area, horizon, bottom, point.dRel, point.yRel, tmpPoint);
    float depth = clamp(point.dRel / 140f, 0f, 1f);
    float width = area.width() * (0.060f * (1f - depth) + 0.014f);
    float height = width * 0.46f;
    int alpha = point.vehicleCandidate ? 220 : 120;
    int body = point.vehicleCandidate ? Color.argb(alpha, 36, 230, 170) : Color.argb(alpha, 112, 140, 168);
    if (point.vRel < -2.5f) {
      body = Color.argb(alpha, 255, 180, 70);
    }
    paint.setStyle(Paint.Style.FILL);
    paint.setColor(body);
    rect.set(tmpPoint.x - width * 0.5f, tmpPoint.y - height, tmpPoint.x + width * 0.5f, tmpPoint.y);
    canvas.drawRoundRect(rect, height * 0.18f, height * 0.18f, paint);
    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(1.8f);
    paint.setColor(Color.argb(alpha, 210, 255, 245));
    canvas.drawRoundRect(rect, height * 0.18f, height * 0.18f, paint);

    if (point.vehicleCandidate && point.dRel < 70f) {
      mono.setTextAlign(Paint.Align.CENTER);
      mono.setColor(Color.rgb(224, 255, 245));
      fitText(mono, point.label + " " + whole(point.dRel) + "m", width * 2.0f, 14f, 8f);
      canvas.drawText(point.label + " " + whole(point.dRel) + "m", tmpPoint.x, rect.top - 7f, mono);
    }
  }

  private void drawSelfCar(Canvas canvas, RectF area, float bottom, boolean active) {
    float carW = area.width() * 0.105f;
    float carH = area.height() * 0.112f;
    float cx = area.centerX();
    float top = bottom - carH * 0.82f;
    Path car = new Path();
    car.moveTo(cx - carW * 0.40f, bottom);
    car.lineTo(cx - carW * 0.50f, top + carH * 0.42f);
    car.lineTo(cx - carW * 0.30f, top);
    car.lineTo(cx + carW * 0.30f, top);
    car.lineTo(cx + carW * 0.50f, top + carH * 0.42f);
    car.lineTo(cx + carW * 0.40f, bottom);
    car.close();
    paint.setStyle(Paint.Style.FILL);
    paint.setColor(active ? Color.rgb(210, 228, 255) : Color.rgb(130, 145, 168));
    canvas.drawPath(car, paint);
    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(2.2f);
    paint.setColor(Color.rgb(92, 145, 255));
    canvas.drawPath(car, paint);
    paint.setStyle(Paint.Style.FILL);
    paint.setColor(Color.rgb(255, 84, 115));
    rect.set(cx - carW * 0.32f, bottom - carH * 0.15f, cx + carW * 0.32f, bottom - carH * 0.11f);
    canvas.drawRoundRect(rect, 2f, 2f, paint);
  }

  private void project(RectF area, float horizon, float bottom, float distance, float lateral, PointF out) {
    float d = clamp(distance, 0f, 115f);
    float t = (float) Math.sqrt(d / 115f);
    float y = bottom - (bottom - horizon) * t;
    float scale = area.width() * (0.108f * (1f - t) + 0.010f);
    out.x = area.centerX() + lateral * scale;
    out.y = y;
  }

  private void drawRightPanel(Canvas canvas, RectF area, HudTelemetry t, long now) {
    float pad = area.width() * 0.095f;
    RectF card = new RectF(area.left + pad, area.top + area.height() * 0.13f, area.right - pad, area.bottom - area.height() * 0.12f);
    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(2.2f);
    paint.setColor(Color.argb(190, 72, 112, 235));
    canvas.drawRoundRect(card, 8f, 8f, paint);

    RectF preview = new RectF(card.left + pad * 0.35f, card.top + pad * 0.35f, card.right - pad * 0.35f, card.top + card.height() * 0.43f);
    drawCameraPreview(canvas, preview, t, now);

    mono.setTextAlign(Paint.Align.LEFT);
    mono.setColor(Color.rgb(152, 188, 255));
    fitText(mono, "ROUTE DATA", preview.width(), area.height() * 0.025f, 12f);
    canvas.drawText("ROUTE DATA", preview.left, preview.bottom + area.height() * 0.045f, mono);

    mono.setColor(Color.rgb(230, 240, 255));
    fitText(mono, "v " + whole(t.speedKph) + " km/h  set " + whole(t.setSpeedKph), preview.width(), area.height() * 0.021f, 10f);
    float y = preview.bottom + area.height() * 0.080f;
    long ageMs = Math.max(0L, System.currentTimeMillis() - t.generatedAtMs);
    canvas.drawText("v " + whole(t.speedKph) + " km/h  set " + whole(t.setSpeedKph), preview.left, y, mono);
    canvas.drawText("a " + signed(t.accel) + "  gap " + one(t.tFollow) + "s / " + whole(t.desiredDistance) + "m", preview.left, y + mono.getTextSize() * 1.35f, mono);
    canvas.drawText("lane L" + t.leftLaneLine + " R" + t.rightLaneLine + "  age " + ageMs + "ms", preview.left, y + mono.getTextSize() * 2.70f, mono);
    canvas.drawText("xState " + t.xState + "  gear " + t.gear, preview.left, y + mono.getTextSize() * 4.05f, mono);
    if (!t.navMain.isEmpty() || !t.navDistance.isEmpty()) {
      canvas.drawText(t.navDistance + " " + t.navMain, preview.left, y + mono.getTextSize() * 5.40f, mono);
    }
    drawCpuBars(canvas, preview.left, card.bottom - area.height() * 0.17f, preview.width(), area.height() * 0.13f, t);
  }

  private void drawCpuBars(Canvas canvas, float left, float top, float width, float height, HudTelemetry t) {
    HudTelemetry.CpuStats stats = t.cpuStats;
    mono.setTextAlign(Paint.Align.LEFT);
    mono.setColor(Color.rgb(152, 188, 255));
    fitText(mono, "CPU CORES", width, 16f, 9f);
    canvas.drawText("CPU CORES", left, top, mono);
    if (!stats.available || stats.cores.length == 0) {
      mono.setColor(Color.rgb(170, 184, 205));
      canvas.drawText("waiting", left, top + height * 0.44f, mono);
      return;
    }

    int cores = Math.min(stats.cores.length, 8);
    float gap = 4f;
    float barH = (height - 20f - gap * (cores - 1)) / Math.max(1, cores);
    float labelW = width * 0.13f;
    for (int i = 0; i < cores; i++) {
      float value = stats.cores[i];
      float y = top + 18f + i * (barH + gap);
      mono.setColor(Color.rgb(150, 166, 188));
      fitText(mono, String.valueOf(i), labelW, barH * 0.9f, 7f);
      canvas.drawText(String.valueOf(i), left, y + barH * 0.82f, mono);

      float barLeft = left + labelW;
      float barW = width - labelW - width * 0.18f;
      paint.setStyle(Paint.Style.FILL);
      paint.setColor(Color.argb(120, 45, 58, 78));
      rect.set(barLeft, y, barLeft + barW, y + barH);
      canvas.drawRoundRect(rect, 3f, 3f, paint);
      if (value >= 0f) {
        int color = value > 75f ? Color.rgb(255, 94, 105) : value > 45f ? Color.rgb(255, 190, 82) : Color.rgb(52, 235, 184);
        paint.setColor(Color.argb(220, Color.red(color), Color.green(color), Color.blue(color)));
        rect.set(barLeft, y, barLeft + barW * value / 100f, y + barH);
        canvas.drawRoundRect(rect, 3f, 3f, paint);
      }
      mono.setTextAlign(Paint.Align.RIGHT);
      mono.setColor(Color.rgb(214, 226, 244));
      fitText(mono, value < 0f ? "--" : whole(value), width * 0.16f, barH * 0.9f, 7f);
      canvas.drawText(value < 0f ? "--" : whole(value), left + width, y + barH * 0.82f, mono);
      mono.setTextAlign(Paint.Align.LEFT);
    }

    if (stats.memoryPercent > 0f) {
      mono.setColor(Color.rgb(150, 166, 188));
      fitText(mono, "MEM " + whole(stats.memoryPercent) + "%", width, 12f, 8f);
      canvas.drawText("MEM " + whole(stats.memoryPercent) + "%", left + width * 0.58f, top, mono);
    }
  }

  private void drawCameraPreview(Canvas canvas, RectF preview, HudTelemetry t, long now) {
    paint.setStyle(Paint.Style.FILL);
    LinearGradient sky = new LinearGradient(0, preview.top, 0, preview.bottom, Color.rgb(69, 147, 224), Color.rgb(22, 30, 48), Shader.TileMode.CLAMP);
    paint.setShader(sky);
    canvas.drawRoundRect(preview, 5f, 5f, paint);
    paint.setShader(null);

    float horizon = preview.top + preview.height() * 0.50f;
    paint.setColor(Color.rgb(37, 42, 49));
    Path road = new Path();
    road.moveTo(preview.centerX() - preview.width() * 0.07f, horizon);
    road.lineTo(preview.right, preview.bottom);
    road.lineTo(preview.left, preview.bottom);
    road.close();
    canvas.drawPath(road, paint);

    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(2f);
    paint.setColor(Color.argb(180, 240, 250, 255));
    canvas.drawLine(preview.centerX() - preview.width() * 0.02f, horizon, preview.centerX() - preview.width() * 0.22f, preview.bottom, paint);
    canvas.drawLine(preview.centerX() + preview.width() * 0.02f, horizon, preview.centerX() + preview.width() * 0.22f, preview.bottom, paint);
    paint.setColor(Color.argb(210, 255, 240, 120));
    float pulse = (now % 900) / 900f;
    canvas.drawLine(preview.centerX(), horizon + pulse * preview.height() * 0.35f, preview.centerX(), horizon + preview.height() * 0.12f + pulse * preview.height() * 0.35f, paint);

    if (t.leftBlindspot || t.rightBlindspot) {
      paint.setStyle(Paint.Style.FILL);
      paint.setColor(Color.argb(210, 255, 80, 98));
      float r = preview.height() * 0.065f;
      canvas.drawCircle(t.leftBlindspot ? preview.left + r * 2f : preview.right - r * 2f, preview.top + r * 1.7f, r, paint);
    }
  }

  private void drawAlerts(Canvas canvas, int w, int h, HudTelemetry t) {
    if (t.alertText1.isEmpty() && t.alertText2.isEmpty()) {
      return;
    }
    String text = t.alertText1.isEmpty() ? t.alertText2 : t.alertText1;
    paint.setStyle(Paint.Style.FILL);
    paint.setColor(Color.argb(170, 255, 70, 95));
    rect.set(w * 0.34f, h * 0.83f, w * 0.66f, h * 0.91f);
    canvas.drawRoundRect(rect, 8f, 8f, paint);
    mono.setTextAlign(Paint.Align.CENTER);
    mono.setColor(Color.WHITE);
    fitText(mono, text, rect.width() * 0.9f, h * 0.035f, 14f);
    canvas.drawText(text, rect.centerX(), rect.centerY() + mono.getTextSize() * 0.35f, mono);
  }

  private void drawStatus(Canvas canvas, int w, int h) {
    mono.setTextAlign(Paint.Align.LEFT);
    mono.setColor(connected ? Color.rgb(42, 244, 198) : Color.rgb(255, 86, 120));
    fitText(mono, connectionState, w * 0.28f, h * 0.021f, 10f);
    canvas.drawText(connectionState, w * 0.012f, h * 0.975f, mono);
  }

  private void fitText(Paint p, String text, float maxWidth, float desired, float min) {
    float size = desired;
    p.setTextSize(size);
    while (size > min && p.measureText(text) > maxWidth) {
      size -= 1f;
      p.setTextSize(size);
    }
  }

  private static float clamp(float value, float min, float max) {
    return Math.max(min, Math.min(max, value));
  }

  private static String whole(float value) {
    return String.valueOf(Math.round(value));
  }

  private static String signed(float value) {
    return String.format(value >= 0 ? "+%.2f" : "%.2f", value);
  }

  private static String one(float value) {
    return String.format("%.1f", value);
  }
}
