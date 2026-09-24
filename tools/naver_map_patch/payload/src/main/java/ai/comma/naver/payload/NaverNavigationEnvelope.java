package ai.comma.naver.payload;

public final class NaverNavigationEnvelope {
  private NaverNavigationEnvelope() {
  }

  public static String toJson(NaverNavigationState state) {
    if (state == null || !state.isFrameEligible()) {
      return "";
    }
    StringBuilder builder = new StringBuilder(512);
    builder.append('{');
    field(builder, "schema", "naver.navigation.v1");
    builder.append(',');
    field(builder, "sessionId", state.sessionId);
    builder.append(",\"sequence\":").append(state.sequence);
    builder.append(",\"sentMonotonicMs\":").append(state.sentMonotonicMs);
    builder.append(',');
    field(builder, "lifecycle", state.lifecycle);
    builder.append(",\"guidance\":{");
    builder.append("\"current\":");
    appendGuidance(builder, state.current);
    builder.append(",\"next\":");
    appendGuidance(builder, state.next);
    builder.append('}');
    builder.append(",\"safety\":");
    appendSafety(builder, state.safety, state.safetyRevision);
    builder.append(",\"road\":{\"limitValid\":false,\"categoryValid\":false}");
    builder.append(",\"route\":");
    appendRoute(builder, state.route, state.routeRevision);
    builder.append('}');
    return builder.toString();
  }

  private static void appendGuidance(
      StringBuilder builder, Naver6805ObjectMapper.Guidance guidance) {
    if (guidance == null || !guidance.present) {
      builder.append("{\"present\":false}");
      return;
    }
    builder.append("{\"present\":true,");
    field(builder, "maneuver", guidance.maneuver);
    builder.append(",\"distanceM\":").append(number(guidance.distanceM));
    builder.append(',');
    field(builder, "roadName", guidance.roadName);
    builder.append(',');
    field(builder, "mainText", guidance.mainText);
    builder.append('}');
  }

  private static void appendSafety(
      StringBuilder builder, Naver6805ObjectMapper.Safety safety, long revision) {
    if (safety == null || !safety.present) {
      builder.append("{\"present\":false}");
      return;
    }
    builder.append("{\"present\":true,");
    field(builder, "kind", safety.kind);
    builder.append(",\"distanceM\":").append(number(safety.distanceM));
    if (!"speed_bump".equals(safety.kind)) {
      builder.append(",\"speedKph\":").append(safety.speedKph);
    }
    if (revision > 0L) {
      builder.append(",\"revision\":").append(revision);
    }
    builder.append('}');
  }

  private static void appendRoute(
      StringBuilder builder, Naver6805ObjectMapper.Route route, long revision) {
    if (route == null || !route.present) {
      builder.append("{\"present\":false}");
      return;
    }
    builder.append("{\"present\":true,\"revision\":").append(revision);
    builder.append(",\"remainingDistanceM\":0");
    builder.append(",\"remainingTimeSec\":0");
    builder.append(",\"offRoute\":false");
    builder.append(",\"destinationValid\":false");
    builder.append(",\"points\":[");
    for (int index = 0; index < route.points.size(); index++) {
      if (index > 0) {
        builder.append(',');
      }
      Naver6805ObjectMapper.RoutePoint point = route.points.get(index);
      builder.append('[')
          .append(number(point.latitude))
          .append(',')
          .append(number(point.longitude))
          .append(']');
    }
    builder.append("]}");
  }

  private static void field(StringBuilder builder, String key, String value) {
    builder.append('"').append(key).append("\":\"");
    escape(builder, value == null ? "" : value);
    builder.append('"');
  }

  private static String number(double value) {
    if (Math.rint(value) == value) {
      return Long.toString((long) value);
    }
    return Double.toString(value);
  }

  private static void escape(StringBuilder builder, String value) {
    for (int i = 0; i < value.length(); i++) {
      char ch = value.charAt(i);
      if (ch == '"' || ch == '\\') {
        builder.append('\\').append(ch);
      } else if (ch == '\n') {
        builder.append("\\n");
      } else if (ch == '\r') {
        builder.append("\\r");
      } else if (ch == '\t') {
        builder.append("\\t");
      } else if (ch < 0x20) {
        String hex = Integer.toHexString(ch);
        builder.append("\\u");
        for (int pad = hex.length(); pad < 4; pad++) {
          builder.append('0');
        }
        builder.append(hex);
      } else {
        builder.append(ch);
      }
    }
  }
}
