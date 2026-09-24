package ai.comma.naver.payload;

import java.util.Optional;

public class ProductionRuntime {
  private static final long SAFETY_PAIR_MAX_AGE_MS = 1000L;
  private final NaverNavigationAggregator aggregator;
  private final NaverNavigationSender sender;
  private final boolean captureMappingOutcomes;
  private final Object enqueueLock = new Object();
  private final ThreadLocal<PendingSafetySource> pendingSafetySource =
      new ThreadLocal<PendingSafetySource>();
  private final ThreadLocal<MappingOutcome> mappingOutcome =
      new ThreadLocal<MappingOutcome>();

  public ProductionRuntime() {
    this(new NaverNavigationAggregator(), NaverNavigationSender.createDefault(), false);
  }

  public ProductionRuntime(boolean captureMappingOutcomes) {
    this(
        new NaverNavigationAggregator(),
        NaverNavigationSender.createDefault(),
        captureMappingOutcomes);
  }

  public ProductionRuntime(NaverNavigationAggregator aggregator, NaverNavigationSender sender) {
    this(aggregator, sender, false);
  }

  public ProductionRuntime(
      NaverNavigationAggregator aggregator,
      NaverNavigationSender sender,
      boolean captureMappingOutcomes) {
    this.aggregator = aggregator;
    this.sender = sender;
    this.captureMappingOutcomes = captureMappingOutcomes;
  }

  public void onStatus(Object status) {
    enqueue("status", status);
  }

  public void onCurrentTbt(Object item) {
    enqueue("tbt_current", item);
  }

  public void onNextTbt(Object item) {
    enqueue("tbt_next", item);
  }

  public void onRoute(Object route) {
    clearMappingOutcome();
    long now = nowMs();
    Naver6805ObjectMapper.Route mapped = Naver6805ObjectMapper.mapRoute(route);
    NaverNavigationState state = apply(
        Naver6805ObjectMapper.MappedUpdate.route(mapped), now);
    captureMappingOutcome(new MappingOutcome(
        "route",
        mapped.outcome,
        rootDescriptor(route),
        mapped.inputCount,
        mapped.outputCount,
        state.routeRevision,
        mapped.present,
        false,
        state.isFrameEligible()));
  }

  public void onSafetySource(Object item) {
    clearMappingOutcome();
    synchronized (enqueueLock) {
      long now = nowMs();
      NaverNavigationState state = aggregator.snapshot(now);
      Naver6805ObjectMapper.SafetySource source = Naver6805ObjectMapper.mapSafetySource(item);
      PendingSafetySource previous = pendingSafetySource.get();
      // The final display object has no event identity. Multiple source objects
      // in the same construction window are ambiguous, even with equal codes.
      if (!"guiding".equals(state.lifecycle)) {
        source = Naver6805ObjectMapper.SafetySource.absent("safety_source_inactive");
      } else if (previous != null) {
        source = Naver6805ObjectMapper.SafetySource.absent("safety_source_ambiguous");
      }
      long outstanding = previous == null ? 1L
          : previous.outstanding == Long.MAX_VALUE ? Long.MAX_VALUE : previous.outstanding + 1L;
      pendingSafetySource.set(new PendingSafetySource(source, state.sessionId, now, outstanding));
      captureMappingOutcome(new MappingOutcome(
          "safety", source.outcome, rootDescriptor(item), 0, 0, state.safetyRevision,
          source.present, source.present && source.distanceM > 0.0, state.isFrameEligible()));
    }
  }

  public void onSafety(Object item) {
    clearMappingOutcome();
    try {
      synchronized (enqueueLock) {
        long now = nowMs();
        PendingSafetySource pending = pendingSafetySource.get();
        NaverNavigationState current = aggregator.snapshot(now);
        Naver6805ObjectMapper.SafetySource source = pending != null && pending.matches(current, now)
            ? pending.source : Naver6805ObjectMapper.SafetySource.absent();
        Naver6805ObjectMapper.Safety mapped = Naver6805ObjectMapper.mapSafety(item, source);
        NaverNavigationState state = apply(Naver6805ObjectMapper.MappedUpdate.safety(mapped), now);
        captureMappingOutcome(new MappingOutcome(
            "safety", mapped.present ? "safety_ok" : "safety_rejected", rootDescriptor(item),
            0, 0, state.safetyRevision, mapped.present, mapped.present && mapped.distanceM > 0.0,
            state.isFrameEligible()));
      }
    } finally {
      PendingSafetySource pending = pendingSafetySource.get();
      if (pending != null && pending.outstanding > 1L) {
        // Do not let an older outstanding final consume a newly captured source.
        long remaining = pending.outstanding == Long.MAX_VALUE ? Long.MAX_VALUE : pending.outstanding - 1L;
        pendingSafetySource.set(new PendingSafetySource(
            Naver6805ObjectMapper.SafetySource.absent("safety_source_ambiguous"),
            pending.sessionId, pending.receivedMs, remaining));
      } else {
        pendingSafetySource.remove();
      }
    }
  }

  public void enqueue(String channel, Object value) {
    long now = nowMs();
    Optional<Naver6805ObjectMapper.MappedUpdate> update =
        Naver6805ObjectMapper.map(channel, value);
    if (!update.isPresent()) {
      return;
    }
    apply(update.get(), now);
  }

  public Object takeMappingOutcome() {
    MappingOutcome value = mappingOutcome.get();
    mappingOutcome.remove();
    return value;
  }

  private NaverNavigationState apply(
      Naver6805ObjectMapper.MappedUpdate update, long now) {
    synchronized (enqueueLock) {
      NaverNavigationState state = aggregator.apply(update, now);
      sender.offer(state);
      return state;
    }
  }

  private void clearMappingOutcome() {
    if (captureMappingOutcomes) {
      mappingOutcome.remove();
    }
  }

  private void captureMappingOutcome(MappingOutcome value) {
    if (captureMappingOutcomes) {
      mappingOutcome.set(value);
    }
  }

  private static String rootDescriptor(Object value) {
    if (value == null) {
      return "Ljava/lang/Object;";
    }
    String name = value.getClass().getName().replace('.', '/');
    if (name.length() > 240 || !name.matches("[A-Za-z0-9_$/]+")) {
      return "Ljava/lang/Object;";
    }
    return "L" + name + ";";
  }

  public String snapshotJson() {
    return NaverNavigationEnvelope.toJson(aggregator.snapshot(nowMs()));
  }

  private static long nowMs() {
    return System.nanoTime() / 1000000L;
  }

  private static final class PendingSafetySource {
    final Naver6805ObjectMapper.SafetySource source;
    final String sessionId;
    final long receivedMs;
    final long outstanding;

    PendingSafetySource(Naver6805ObjectMapper.SafetySource source, String sessionId, long receivedMs, long outstanding) {
      this.source = source;
      this.sessionId = sessionId;
      this.receivedMs = receivedMs;
      this.outstanding = outstanding;
    }

    boolean matches(NaverNavigationState state, long now) {
      long age = now - receivedMs;
      return outstanding == 1L && "guiding".equals(state.lifecycle) && sessionId.equals(state.sessionId)
          && age >= 0L && age < SAFETY_PAIR_MAX_AGE_MS;
    }
  }

  public static final class MappingOutcome {
    public final String channel;
    public final String result;
    public final String rootDescriptor;
    public final int inputCount;
    public final int outputCount;
    public final long revision;
    public final boolean itemPresent;
    public final boolean distanceValid;
    public final boolean frameEligible;

    public MappingOutcome(
        String channel,
        String result,
        String rootDescriptor,
        int inputCount,
        int outputCount,
        long revision,
        boolean itemPresent,
        boolean distanceValid,
        boolean frameEligible) {
      if (!("route".equals(channel) || "safety".equals(channel))
          || result == null
          || !result.matches("[a-z][a-z0-9_]{0,63}")
          || rootDescriptor == null
          || rootDescriptor.length() > 256
          || !rootDescriptor.matches("L[A-Za-z0-9_$/]+;")
          || inputCount < 0
          || outputCount < 0
          || revision < 0L) {
        throw new IllegalArgumentException("invalid mapping outcome");
      }
      this.channel = channel;
      this.result = result;
      this.rootDescriptor = rootDescriptor;
      this.inputCount = inputCount;
      this.outputCount = outputCount;
      this.revision = revision;
      this.itemPresent = itemPresent;
      this.distanceValid = distanceValid;
      this.frameEligible = frameEligible;
    }
  }
}
