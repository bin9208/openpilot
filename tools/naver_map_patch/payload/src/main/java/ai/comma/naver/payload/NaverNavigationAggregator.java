package ai.comma.naver.payload;

import java.util.UUID;

public final class NaverNavigationAggregator {
  public interface SessionIds {
    String next();
  }

  public static final class RandomSessionIds implements SessionIds {
    @Override
    public String next() {
      return UUID.randomUUID().toString();
    }
  }

  public static final class FixedSessionIds implements SessionIds {
    private final String[] ids;
    private int index;

    public FixedSessionIds(String... ids) {
      this.ids = ids == null ? new String[0] : ids.clone();
    }

    @Override
    public synchronized String next() {
      if (index < ids.length) {
        return ids[index++];
      }
      return UUID.randomUUID().toString();
    }
  }

  private final SessionIds sessionIds;
  private String sessionId;
  private long sequence;
  private String lifecycle = "idle";
  private boolean terminal;
  private Naver6805ObjectMapper.Guidance current = Naver6805ObjectMapper.Guidance.absent();
  private Naver6805ObjectMapper.Guidance next = Naver6805ObjectMapper.Guidance.absent();
  private Naver6805ObjectMapper.Safety safety = Naver6805ObjectMapper.Safety.absent();
  private long safetyRevision;
  private Naver6805ObjectMapper.Route route = Naver6805ObjectMapper.Route.absent();
  private long routeRevision;
  private Naver6805ObjectMapper.Route initialRoute;
  private long initialRouteAt;

  public NaverNavigationAggregator() {
    this(new RandomSessionIds());
  }

  public NaverNavigationAggregator(SessionIds sessionIds) {
    this.sessionIds = sessionIds == null ? new RandomSessionIds() : sessionIds;
  }

  public synchronized void onStatus(Object status, long monotonicMs) {
    String mapped = Naver6805ObjectMapper.mapLifecycle(status);
    apply(Naver6805ObjectMapper.MappedUpdate.status(mapped), monotonicMs);
  }

  public synchronized void onCurrentGuidance(Object item, long monotonicMs) {
    if (!isAcceptingControl()) {
      return;
    }
    apply(Naver6805ObjectMapper.MappedUpdate.current(
        Naver6805ObjectMapper.mapGuidance(item)), monotonicMs);
  }

  public synchronized void onNextGuidance(Object item, long monotonicMs) {
    if (!isAcceptingControl()) {
      return;
    }
    apply(Naver6805ObjectMapper.MappedUpdate.next(
        Naver6805ObjectMapper.mapGuidance(item)), monotonicMs);
  }

  public synchronized void onSafety(Object item, long monotonicMs) {
    if (!isAcceptingControl()) {
      return;
    }
    apply(Naver6805ObjectMapper.MappedUpdate.safety(
        Naver6805ObjectMapper.mapSafety(item)), monotonicMs);
  }

  public synchronized NaverNavigationState apply(
      Naver6805ObjectMapper.MappedUpdate update, long monotonicMs) {
    if (update == null) {
      return snapshotLocked(monotonicMs);
    }
    if ("status".equals(update.channel)) {
      applyLifecycle(update.lifecycle, monotonicMs);
      return snapshotLocked(monotonicMs);
    }
    if (!isAcceptingControl()) {
      // Initial route calculation can precede the first Guiding callback.
      // Never adopt late control from a terminal session into its successor.
      if (!terminal && "idle".equals(lifecycle) && "route".equals(update.channel)) {
        initialRoute = update.route;
        initialRouteAt = monotonicMs;
      }
      return snapshotLocked(monotonicMs);
    }
    if ("tbt_current".equals(update.channel)) {
      current = update.guidance == null
          ? Naver6805ObjectMapper.Guidance.absent() : update.guidance;
      bumpSequence();
    } else if ("tbt_next".equals(update.channel)) {
      next = update.guidance == null
          ? Naver6805ObjectMapper.Guidance.absent() : update.guidance;
      bumpSequence();
    } else if ("safety".equals(update.channel)) {
      Naver6805ObjectMapper.Safety updatedSafety = update.safety == null
          ? Naver6805ObjectMapper.Safety.absent() : update.safety;
      if (!sameSafety(safety, updatedSafety)) {
        safety = updatedSafety;
        if (safety.present) {
          bumpSafetyRevision();
        }
      }
      bumpSequence();
    } else if ("route".equals(update.channel)) {
      Naver6805ObjectMapper.Route updatedRoute = update.route == null
          ? Naver6805ObjectMapper.Route.absent() : update.route;
      if (!sameRoute(route, updatedRoute)) {
        route = updatedRoute;
        bumpRouteRevision();
      }
      bumpSequence();
    }
    return snapshotLocked(monotonicMs);
  }

  public synchronized NaverNavigationState snapshot(long monotonicMs) {
    return snapshotLocked(monotonicMs);
  }

  private NaverNavigationState snapshotLocked(long monotonicMs) {
    ensureSession();
    if ("guiding".equals(lifecycle)) {
      return NaverNavigationState.guiding(
          sessionId,
          sequence,
          monotonicMs,
          current,
          next,
          safety,
          safetyRevision,
          route,
          routeRevision);
    }
    return NaverNavigationState.terminal(sessionId, sequence, monotonicMs, lifecycle);
  }

  private boolean isAcceptingControl() {
    return "guiding".equals(lifecycle) && !terminal && sessionId != null;
  }

  private void applyLifecycle(String mapped, long monotonicMs) {
    if (!"guiding".equals(mapped) && !"stopped".equals(mapped) && !"arrived".equals(mapped)) {
      return;
    }
    if ("guiding".equals(mapped)) {
      if (sessionId == null || terminal) {
        sessionId = sessionIds.next();
        current = Naver6805ObjectMapper.Guidance.absent();
        next = Naver6805ObjectMapper.Guidance.absent();
        safety = Naver6805ObjectMapper.Safety.absent();
        safetyRevision = 0L;
        route = Naver6805ObjectMapper.Route.absent();
        routeRevision = 0L;
      }
      lifecycle = "guiding";
      terminal = false;
      if (initialRoute != null && initialRoute.present
          && monotonicMs >= initialRouteAt && monotonicMs - initialRouteAt < 5000L) {
        route = initialRoute;
        bumpRouteRevision();
      }
      initialRoute = null;
      bumpSequence();
      return;
    }
    ensureSession();
    lifecycle = mapped;
    terminal = true;
    initialRoute = null;
    current = Naver6805ObjectMapper.Guidance.absent();
    next = Naver6805ObjectMapper.Guidance.absent();
    safety = Naver6805ObjectMapper.Safety.absent();
    safetyRevision = 0L;
    route = Naver6805ObjectMapper.Route.absent();
    routeRevision = 0L;
    bumpSequence();
  }

  private void ensureSession() {
    if (sessionId == null) {
      sessionId = sessionIds.next();
    }
  }

  private void bumpSequence() {
    if (sequence < Long.MAX_VALUE) {
      sequence++;
    }
  }

  private void bumpSafetyRevision() {
    if (safetyRevision < Long.MAX_VALUE) {
      safetyRevision++;
    }
  }

  private void bumpRouteRevision() {
    if (routeRevision < Long.MAX_VALUE) {
      routeRevision++;
    }
  }

  private static boolean sameSafety(
      Naver6805ObjectMapper.Safety left, Naver6805ObjectMapper.Safety right) {
    return left.present == right.present
        && left.kind.equals(right.kind)
        && Double.compare(left.distanceM, right.distanceM) == 0
        && left.speedKph == right.speedKph;
  }

  private static boolean sameRoute(
      Naver6805ObjectMapper.Route left, Naver6805ObjectMapper.Route right) {
    if (left.present != right.present) {
      return false;
    }
    if (!left.present) {
      return true;
    }
    if (left.points.size() != right.points.size()) {
      return false;
    }
    for (int index = 0; index < left.points.size(); index++) {
      Naver6805ObjectMapper.RoutePoint leftPoint = left.points.get(index);
      Naver6805ObjectMapper.RoutePoint rightPoint = right.points.get(index);
      if (Double.compare(leftPoint.latitude, rightPoint.latitude) != 0
          || Double.compare(leftPoint.longitude, rightPoint.longitude) != 0) {
        return false;
      }
    }
    return true;
  }
}
