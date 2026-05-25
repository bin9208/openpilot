package ai.comma.galaxyhud;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

final class HudTelemetry {
  static final class Line {
    final float[] x;
    final float[] y;

    Line(float[] x, float[] y) {
      this.x = x;
      this.y = y;
    }

    int size() {
      return Math.min(x.length, y.length);
    }
  }

  static final class Lead {
    final boolean status;
    final float dRel;
    final float yRel;
    final float vRel;
    final float aLead;
    final boolean fcw;
    final boolean radar;

    Lead(boolean status, float dRel, float yRel, float vRel, float aLead, boolean fcw, boolean radar) {
      this.status = status;
      this.dRel = dRel;
      this.yRel = yRel;
      this.vRel = vRel;
      this.aLead = aLead;
      this.fcw = fcw;
      this.radar = radar;
    }
  }

  static final class RadarPoint {
    final String label;
    final float dRel;
    final float yRel;
    final float vRel;
    final float vAbsKph;
    final float probability;
    final int validCount;
    final boolean vehicleCandidate;

    RadarPoint(String label, float dRel, float yRel, float vRel, float vAbsKph, float probability, int validCount, boolean vehicleCandidate) {
      this.label = label;
      this.dRel = dRel;
      this.yRel = yRel;
      this.vRel = vRel;
      this.vAbsKph = vAbsKph;
      this.probability = probability;
      this.validCount = validCount;
      this.vehicleCandidate = vehicleCandidate;
    }
  }

  static final class CpuStats {
    final boolean available;
    final float memoryPercent;
    final float[] cores;

    CpuStats(boolean available, float memoryPercent, float[] cores) {
      this.available = available;
      this.memoryPercent = memoryPercent;
      this.cores = cores;
    }
  }

  long generatedAtMs;
  boolean connected;
  boolean enabled;
  boolean active;
  boolean experimentalMode;
  boolean leftBlinker;
  boolean rightBlinker;
  boolean leftBlindspot;
  boolean rightBlindspot;
  boolean standstill;
  int leftLaneLine;
  int rightLaneLine;
  float speedKph;
  float setSpeedKph;
  float accel;
  float steeringAngleDeg;
  float speedLimitKph;
  float desiredDistance;
  float tFollow;
  int xState;
  String alertText1 = "";
  String alertText2 = "";
  String gear = "";
  String navMain = "";
  String navDistance = "";
  String navTurnType = "";
  CpuStats cpuStats = new CpuStats(false, 0f, new float[0]);
  Line path;
  final ArrayList<Line> laneLines = new ArrayList<>();
  final ArrayList<Line> roadEdges = new ArrayList<>();
  final ArrayList<Lead> leads = new ArrayList<>();
  final ArrayList<RadarPoint> radarPoints = new ArrayList<>();

  static HudTelemetry fromJson(JSONObject root) {
    HudTelemetry t = new HudTelemetry();
    JSONObject meta = root.optJSONObject("meta");
    t.generatedAtMs = meta == null ? System.currentTimeMillis() : meta.optLong("generatedAtMs", System.currentTimeMillis());
    t.connected = true;

    JSONObject runtime = root.optJSONObject("runtime");
    t.cpuStats = cpuStatsFrom(obj(runtime, "systemStats"));

    JSONObject services = root.optJSONObject("services");
    JSONObject car = obj(services, "carState");
    JSONObject selfdrive = obj(services, "selfdriveState");
    JSONObject controls = obj(services, "controlsState");
    JSONObject longitudinal = obj(services, "longitudinalPlan");
    JSONObject model = obj(services, "modelV2");
    JSONObject radar = obj(services, "radarState");
    JSONObject canFdRadar = obj(services, "canFdRadar");
    JSONObject carrot = obj(services, "carrotMan");
    JSONObject nav = obj(services, "navInstructionCarrot");

    float vEgo = firstPositive(car.optDouble("vEgoCluster", 0.0), car.optDouble("vEgo", 0.0));
    t.speedKph = mpsToKph(vEgo);
    t.setSpeedKph = clusterSpeedToKph(firstPositive(car.optDouble("vCruiseCluster", 0.0), controls.optDouble("vCruiseCluster", 0.0), controls.optDouble("vCruise", 0.0)));
    t.accel = (float) car.optDouble("aEgo", 0.0);
    t.steeringAngleDeg = (float) car.optDouble("steeringAngleDeg", 0.0);
    t.leftBlinker = car.optBoolean("leftBlinker", false);
    t.rightBlinker = car.optBoolean("rightBlinker", false);
    t.leftBlindspot = car.optBoolean("leftBlindspot", carrot.optBoolean("leftBlindspot", false));
    t.rightBlindspot = car.optBoolean("rightBlindspot", carrot.optBoolean("rightBlindspot", false));
    t.standstill = car.optBoolean("standstill", false);
    t.leftLaneLine = car.optInt("leftLaneLine", 0);
    t.rightLaneLine = car.optInt("rightLaneLine", 0);
    t.gear = car.optString("gearShifter", "");
    t.speedLimitKph = clusterSpeedToKph(firstPositive(car.optDouble("speedLimit", 0.0), nav.optDouble("speedLimit", 0.0)));

    t.enabled = selfdrive.optBoolean("enabled", controls.optBoolean("enabled", false));
    t.active = selfdrive.optBoolean("active", t.enabled);
    t.experimentalMode = selfdrive.optBoolean("experimentalMode", controls.optBoolean("experimentalMode", false));
    t.alertText1 = selfdrive.optString("alertText1", "");
    t.alertText2 = selfdrive.optString("alertText2", "");

    t.tFollow = (float) longitudinal.optDouble("tFollow", carrot.optDouble("tFollow", 0.0));
    t.desiredDistance = (float) longitudinal.optDouble("desiredDistance", carrot.optDouble("desiredDistance", 0.0));
    t.xState = longitudinal.optInt("xState", 0);
    t.navMain = nav.optString("mainText", "");
    t.navDistance = nav.optString("distanceText", "");
    t.navTurnType = nav.optString("turnType", "");

    t.path = lineFrom(obj(model, "position"), 3, 64);
    JSONArray lanes = model.optJSONArray("laneLines");
    if (lanes != null) {
      for (int i = 0; i < lanes.length(); i++) {
        Line line = lineFrom(lanes.optJSONObject(i), 3, 64);
        if (line != null && line.size() > 1) {
          t.laneLines.add(line);
        }
      }
    }
    JSONArray edges = model.optJSONArray("roadEdges");
    if (edges != null) {
      for (int i = 0; i < edges.length(); i++) {
        Line line = lineFrom(edges.optJSONObject(i), 4, 48);
        if (line != null && line.size() > 1) {
          t.roadEdges.add(line);
        }
      }
    }

    addRadarLead(t.leads, obj(radar, "leadOne"));
    addRadarLead(t.leads, obj(radar, "leadTwo"));
    addModelLeads(t.leads, model.optJSONArray("leadsV3"), t.speedKph / 3.6f);
    addCanFdRadarPoints(t.radarPoints, canFdRadar.optJSONArray("vehiclePoints"));
    if (t.radarPoints.isEmpty()) {
      addCanFdRadarPoints(t.radarPoints, canFdRadar.optJSONArray("points"));
    }
    return t;
  }

  static HudTelemetry demo(long nowMs) {
    HudTelemetry t = new HudTelemetry();
    t.generatedAtMs = nowMs;
    t.connected = false;
    t.enabled = true;
    t.active = true;
    t.speedKph = 116 + (float) Math.sin(nowMs / 1200.0) * 4f;
    t.setSpeedKph = 120;
    t.accel = (float) Math.sin(nowMs / 900.0) * 1.4f;
    t.speedLimitKph = 100;
    t.leftLaneLine = 10;
    t.rightLaneLine = 10;
    t.tFollow = 1.5f;
    t.desiredDistance = 42f;
    t.path = syntheticLine(0f);
    t.laneLines.add(syntheticLine(-5.4f));
    t.laneLines.add(syntheticLine(-1.8f));
    t.laneLines.add(syntheticLine(1.8f));
    t.laneLines.add(syntheticLine(5.4f));
    t.roadEdges.add(syntheticLine(-8.4f));
    t.roadEdges.add(syntheticLine(8.4f));
    t.leads.add(new Lead(true, 38f, -1.2f, -2.0f, -0.2f, false, true));
    t.leads.add(new Lead(true, 72f, 2.3f, 0.8f, 0.0f, false, true));
    t.radarPoints.add(new RadarPoint("P03", 24f, 3.2f, -0.4f, 104f, 0.74f, 31, true));
    t.radarPoints.add(new RadarPoint("P08", 45f, -3.6f, 0.2f, 113f, 0.64f, 42, true));
    t.cpuStats = new CpuStats(true, 54f, new float[]{18f, 24f, 36f, 42f, 8f, 14f, 63f, 71f});
    return t;
  }

  private static JSONObject obj(JSONObject parent, String key) {
    JSONObject child = parent == null ? null : parent.optJSONObject(key);
    return child == null ? new JSONObject() : child;
  }

  private static Line lineFrom(JSONObject payload, int stride, int limit) {
    if (payload == null) {
      return null;
    }
    JSONArray xs = payload.optJSONArray("x");
    JSONArray ys = payload.optJSONArray("y");
    if (xs == null || ys == null) {
      return null;
    }
    int count = Math.min(xs.length(), ys.length());
    if (count < 2) {
      return null;
    }
    int step = Math.max(1, stride);
    int outCount = Math.min(limit, (count + step - 1) / step);
    float[] x = new float[outCount];
    float[] y = new float[outCount];
    int out = 0;
    for (int i = 0; i < count && out < outCount; i += step) {
      x[out] = (float) xs.optDouble(i, 0.0);
      y[out] = (float) ys.optDouble(i, 0.0);
      out++;
    }
    if (out == outCount) {
      return new Line(x, y);
    }
    float[] trimmedX = new float[out];
    float[] trimmedY = new float[out];
    System.arraycopy(x, 0, trimmedX, 0, out);
    System.arraycopy(y, 0, trimmedY, 0, out);
    return new Line(trimmedX, trimmedY);
  }

  private static void addRadarLead(List<Lead> leads, JSONObject lead) {
    if (lead == null || !lead.optBoolean("status", false)) {
      return;
    }
    leads.add(new Lead(
        true,
        (float) lead.optDouble("dRel", 0.0),
        (float) lead.optDouble("yRel", 0.0),
        (float) lead.optDouble("vRel", 0.0),
        (float) lead.optDouble("aLeadK", lead.optDouble("aLead", 0.0)),
        lead.optBoolean("fcw", false),
        lead.optBoolean("radar", false)));
  }

  private static void addModelLeads(List<Lead> leads, JSONArray modelLeads, float vEgo) {
    if (modelLeads == null || !leads.isEmpty()) {
      return;
    }
    for (int i = 0; i < modelLeads.length() && i < 2; i++) {
      JSONObject lead = modelLeads.optJSONObject(i);
      if (lead == null || lead.optDouble("prob", 0.0) < 0.45) {
        continue;
      }
      JSONArray x = lead.optJSONArray("x");
      JSONArray y = lead.optJSONArray("y");
      JSONArray v = lead.optJSONArray("v");
      JSONArray a = lead.optJSONArray("a");
      float dist = x == null ? 0f : (float) x.optDouble(0, 0.0);
      float lat = y == null ? 0f : (float) y.optDouble(0, 0.0);
      float vel = v == null ? 0f : (float) v.optDouble(0, vEgo);
      float acc = a == null ? 0f : (float) a.optDouble(0, 0.0);
      leads.add(new Lead(true, dist, lat, vel - vEgo, acc, false, false));
    }
  }

  private static void addCanFdRadarPoints(List<RadarPoint> radarPoints, JSONArray points) {
    if (points == null) {
      return;
    }
    for (int i = 0; i < points.length() && radarPoints.size() < 14; i++) {
      JSONObject point = points.optJSONObject(i);
      if (point == null) {
        continue;
      }
      float dRel = (float) point.optDouble("dRel", 0.0);
      float yRel = (float) point.optDouble("yRel", 0.0);
      if (dRel < 2.5f || dRel > 180f || Math.abs(yRel) > 10f) {
        continue;
      }
      radarPoints.add(new RadarPoint(
          point.optString("label", "R"),
          dRel,
          yRel,
          (float) point.optDouble("vRel", 0.0),
          (float) point.optDouble("vAbsKph", 0.0),
          (float) point.optDouble("probability", 0.0),
          point.optInt("validCount", 0),
          point.optBoolean("vehicleCandidate", false)));
    }
  }

  private static CpuStats cpuStatsFrom(JSONObject stats) {
    if (stats == null || !stats.optBoolean("available", false)) {
      return new CpuStats(false, 0f, new float[0]);
    }
    JSONArray cores = stats.optJSONArray("cores");
    if (cores == null) {
      return new CpuStats(true, (float) stats.optDouble("memoryUsedPercent", 0.0), new float[0]);
    }
    float[] values = new float[cores.length()];
    for (int i = 0; i < cores.length(); i++) {
      if (cores.isNull(i)) {
        values[i] = -1f;
      } else {
        values[i] = Math.max(0f, Math.min(100f, (float) cores.optDouble(i, 0.0)));
      }
    }
    return new CpuStats(true, (float) stats.optDouble("memoryUsedPercent", 0.0), values);
  }

  private static Line syntheticLine(float lateral) {
    int count = 36;
    float[] x = new float[count];
    float[] y = new float[count];
    for (int i = 0; i < count; i++) {
      x[i] = i * 3.2f;
      y[i] = lateral + (float) Math.sin(i * 0.18f) * 0.08f;
    }
    return new Line(x, y);
  }

  private static float firstPositive(double... values) {
    for (double value : values) {
      if (value > 0.01) {
        return (float) value;
      }
    }
    return 0f;
  }

  private static float mpsToKph(double value) {
    return (float) (value * 3.6);
  }

  private static float clusterSpeedToKph(double value) {
    if (value <= 0.01) {
      return 0f;
    }
    return value > 80.0 ? (float) value : mpsToKph(value);
  }
}
