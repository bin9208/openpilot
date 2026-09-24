package ai.comma.naver.payload;

public final class NaverNavigationState {
  public final String sessionId;
  public final long sequence;
  public final long sentMonotonicMs;
  public final String lifecycle;
  public final Naver6805ObjectMapper.Guidance current;
  public final Naver6805ObjectMapper.Guidance next;
  public final Naver6805ObjectMapper.Safety safety;
  public final long safetyRevision;
  public final Naver6805ObjectMapper.Route route;
  public final long routeRevision;

  private NaverNavigationState(
      String sessionId,
      long sequence,
      long sentMonotonicMs,
      String lifecycle,
      Naver6805ObjectMapper.Guidance current,
      Naver6805ObjectMapper.Guidance next,
      Naver6805ObjectMapper.Safety safety,
      long safetyRevision,
      Naver6805ObjectMapper.Route route,
      long routeRevision) {
    this.sessionId = sessionId;
    this.sequence = sequence;
    this.sentMonotonicMs = sentMonotonicMs;
    this.lifecycle = lifecycle;
    this.current = current;
    this.next = next;
    this.safety = safety;
    this.safetyRevision = safetyRevision;
    this.route = route;
    this.routeRevision = routeRevision;
  }

  public static NaverNavigationState idle(String sessionId, long sequence, long sentMonotonicMs) {
    return terminal(sessionId, sequence, sentMonotonicMs, "idle");
  }

  public static NaverNavigationState guiding(
      String sessionId,
      long sequence,
      long sentMonotonicMs,
      Naver6805ObjectMapper.Guidance current,
      Naver6805ObjectMapper.Guidance next,
      Naver6805ObjectMapper.Safety safety) {
    return guiding(
        sessionId,
        sequence,
        sentMonotonicMs,
        current,
        next,
        safety,
        0L,
        Naver6805ObjectMapper.Route.absent(),
        0L);
  }

  public static NaverNavigationState guiding(
      String sessionId,
      long sequence,
      long sentMonotonicMs,
      Naver6805ObjectMapper.Guidance current,
      Naver6805ObjectMapper.Guidance next,
      Naver6805ObjectMapper.Safety safety,
      long safetyRevision) {
    return guiding(
        sessionId,
        sequence,
        sentMonotonicMs,
        current,
        next,
        safety,
        safetyRevision,
        Naver6805ObjectMapper.Route.absent(),
        0L);
  }

  public static NaverNavigationState guiding(
      String sessionId,
      long sequence,
      long sentMonotonicMs,
      Naver6805ObjectMapper.Guidance current,
      Naver6805ObjectMapper.Guidance next,
      Naver6805ObjectMapper.Safety safety,
      long safetyRevision,
      Naver6805ObjectMapper.Route route,
      long routeRevision) {
    return new NaverNavigationState(
        sessionId,
        sequence,
        sentMonotonicMs,
        "guiding",
        current == null ? Naver6805ObjectMapper.Guidance.absent() : current,
        next == null ? Naver6805ObjectMapper.Guidance.absent() : next,
        safety == null ? Naver6805ObjectMapper.Safety.absent() : safety,
        safetyRevision,
        route == null ? Naver6805ObjectMapper.Route.absent() : route,
        routeRevision);
  }

  public static NaverNavigationState terminal(
      String sessionId, long sequence, long sentMonotonicMs, String lifecycle) {
    return new NaverNavigationState(
        sessionId,
        sequence,
        sentMonotonicMs,
        lifecycle,
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent(),
        0L,
        Naver6805ObjectMapper.Route.absent(),
        0L);
  }

  public boolean isTerminal() {
    return "stopped".equals(lifecycle) || "arrived".equals(lifecycle);
  }

  public boolean isFrameEligible() {
    if (!isCanonicalUuid(sessionId)
        || sequence < 0L
        || sentMonotonicMs < 0L
        || safetyRevision < 0L
        || routeRevision < 0L) {
      return false;
    }
    if (!"guiding".equals(lifecycle)
        && !"stopped".equals(lifecycle)
        && !"arrived".equals(lifecycle)) {
      return false;
    }
    if (!"guiding".equals(lifecycle)) {
      return !current.present && !next.present && !safety.present && !route.present;
    }
    return validGuidance(current)
        && validGuidance(next)
        && validSafety(safety)
        && validRoute(route);
  }

  public NaverNavigationState forTransmission(long sequence, long sentMonotonicMs) {
    if ("guiding".equals(lifecycle)) {
      return guiding(
          sessionId,
          sequence,
          sentMonotonicMs,
          current,
          next,
          safety,
          safetyRevision,
          route,
          routeRevision);
    }
    return terminal(sessionId, sequence, sentMonotonicMs, lifecycle);
  }

  private static boolean validGuidance(Naver6805ObjectMapper.Guidance guidance) {
    if (guidance == null || !guidance.present) {
      return true;
    }
    return ("left".equals(guidance.maneuver)
        || "right".equals(guidance.maneuver)
        || "slight_left".equals(guidance.maneuver)
        || "slight_right".equals(guidance.maneuver)
        || "u_turn".equals(guidance.maneuver)
        || "arrive".equals(guidance.maneuver))
        && validDistance(guidance.distanceM, true)
        && validText(guidance.roadName)
        && validText(guidance.mainText);
  }

  private static boolean validSafety(Naver6805ObjectMapper.Safety safety) {
    if (safety == null || !safety.present) {
      return true;
    }
    if (!validDistance(safety.distanceM, false)) {
      return false;
    }
    if ("speed_bump".equals(safety.kind)) {
      return true;
    }
    return ("fixed_camera".equals(safety.kind)
        || "mobile_camera".equals(safety.kind)
        || "section_camera".equals(safety.kind))
        && safety.speedKph > 0
        && safety.speedKph <= 250;
  }

  private static boolean validRoute(Naver6805ObjectMapper.Route route) {
    if (route == null) {
      return false;
    }
    if (!route.present) {
      return route.points.isEmpty() && route.outputCount == 0;
    }
    int pointCount = route.points.size();
    if (pointCount < 2
        || pointCount > 4096
        || route.outputCount != pointCount
        || route.inputCount < pointCount) {
      return false;
    }
    for (Naver6805ObjectMapper.RoutePoint point : route.points) {
      if (point == null
          || !validCoordinate(point.latitude, -90.0, 90.0)
          || !validCoordinate(point.longitude, -180.0, 180.0)) {
        return false;
      }
    }
    return true;
  }

  private static boolean validDistance(double value, boolean allowZero) {
    return !Double.isNaN(value)
        && !Double.isInfinite(value)
        && (allowZero ? value >= 0.0 : value > 0.0)
        && value <= 2000000.0;
  }

  private static boolean validText(String value) {
    return value != null && value.length() <= 256;
  }

  private static boolean validCoordinate(double value, double minimum, double maximum) {
    return !Double.isNaN(value)
        && !Double.isInfinite(value)
        && value >= minimum
        && value <= maximum;
  }

  private static boolean isCanonicalUuid(String value) {
    return value != null
        && value.matches(
            "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
  }
}
