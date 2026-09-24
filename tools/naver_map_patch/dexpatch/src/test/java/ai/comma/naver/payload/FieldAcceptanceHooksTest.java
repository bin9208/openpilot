package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

final class FieldAcceptanceHooksTest {
  @Test
  void sixControlChannelsFanOutOnceWhileLaneRemainsDiagnosticOnly() throws Exception {
    Class<?> hooks = fieldHooks();
    RecordingProductionRuntime production = new RecordingProductionRuntime();
    RecordingHookRuntime diagnostic = new RecordingHookRuntime();
    replaceRuntimes(hooks, production, diagnostic);
    try {
      invoke(hooks, "onStatus", "status-value");
      invoke(hooks, "onCurrentTbt", "current-value");
      invoke(hooks, "onNextTbt", "next-value");
      invoke(hooks, "onSafetySource", "safety-source-value");
      invoke(hooks, "onSafety", "safety-value");
      invoke(hooks, "onRoute", "route-value");
      invoke(hooks, "onLane", "lane-value");
    } finally {
      resetRuntimes(hooks);
    }

    assertEquals(List.of(
        "status", "tbt_current", "tbt_next", "safety_source", "safety", "route"),
        production.channels);
    assertEquals(List.of(
        "status", "tbt_current", "tbt_next", "safety", "safety", "route", "lane"),
        diagnostic.channels);
    assertTrue(diagnostic.values.get(3) instanceof ProductionRuntime.MappingOutcome);
    assertTrue(diagnostic.values.get(4) instanceof ProductionRuntime.MappingOutcome);
    assertTrue(diagnostic.values.get(5) instanceof ProductionRuntime.MappingOutcome);
    assertEquals("lane-value", diagnostic.values.get(6));
    assertFalse(diagnostic.values.contains("safety-source-value"));
    assertFalse(diagnostic.values.contains("safety-value"));
    assertFalse(diagnostic.values.contains("route-value"));
  }

  @Test
  void eachControlFanOutContinuesWhenEitherRuntimeThrows() throws Exception {
    Class<?> hooks = fieldHooks();
    RecordingProductionRuntime throwingProduction = new RecordingProductionRuntime();
    throwingProduction.throwOn = "status";
    RecordingHookRuntime diagnostic = new RecordingHookRuntime();
    replaceRuntimes(hooks, throwingProduction, diagnostic);
    try {
      assertDoesNotThrow(() -> invoke(hooks, "onStatus", "status-value"));
      assertEquals(List.of("status"), diagnostic.channels);

      RecordingProductionRuntime production = new RecordingProductionRuntime();
      RecordingHookRuntime throwingDiagnostic = new RecordingHookRuntime();
      throwingDiagnostic.throwOn = "safety";
      replaceRuntimes(hooks, production, throwingDiagnostic);
      assertDoesNotThrow(() -> invoke(hooks, "onSafety", "safety-value"));
      assertEquals(List.of("safety"), production.channels);
    } finally {
      resetRuntimes(hooks);
    }
  }

  private static Class<?> fieldHooks() {
    return assertDoesNotThrow(
        () -> Class.forName("ai.comma.naver.payload.FieldAcceptanceHooks"));
  }

  private static void replaceRuntimes(
      Class<?> hooks, ProductionRuntime production, HookRuntime diagnostic) throws Exception {
    Method method = hooks.getDeclaredMethod(
        "replaceRuntimesForTest", ProductionRuntime.class, HookRuntime.class);
    method.setAccessible(true);
    method.invoke(null, production, diagnostic);
  }

  private static void resetRuntimes(Class<?> hooks) throws Exception {
    Method method = hooks.getDeclaredMethod("resetRuntimesForTest");
    method.setAccessible(true);
    method.invoke(null);
  }

  private static void invoke(Class<?> hooks, String methodName, Object value) throws Exception {
    hooks.getMethod(methodName, Object.class).invoke(null, value);
  }

  private static final class RecordingProductionRuntime extends ProductionRuntime {
    final List<String> channels = new ArrayList<>();
    String throwOn;
    Object outcome;

    RecordingProductionRuntime() {
      super(new NaverNavigationAggregator(new NaverNavigationAggregator.FixedSessionIds(
          "11111111-1111-1111-1111-111111111111")),
          NaverNavigationSender.noopForTesting());
    }

    @Override
    public void onStatus(Object value) {
      record("status");
    }

    @Override
    public void onCurrentTbt(Object value) {
      record("tbt_current");
    }

    @Override
    public void onNextTbt(Object value) {
      record("tbt_next");
    }

    @Override
    public void onSafetySource(Object value) {
      record("safety_source");
      outcome = mappingOutcome("safety", "safety_source_ok");
    }

    @Override
    public void onSafety(Object value) {
      record("safety");
      outcome = mappingOutcome("safety", "safety_ok");
    }

    @Override
    public void onRoute(Object value) {
      record("route");
      outcome = mappingOutcome("route", "route_ok");
    }

    @Override
    public Object takeMappingOutcome() {
      Object value = outcome;
      outcome = null;
      return value;
    }

    private void record(String channel) {
      channels.add(channel);
      if (channel.equals(throwOn)) {
        throw new IllegalStateException("expected production failure");
      }
    }

    private static ProductionRuntime.MappingOutcome mappingOutcome(
        String channel, String result) {
      return new ProductionRuntime.MappingOutcome(
          channel, result, "Lfixture/Value;", 0, 0, 0L,
          true, true, true);
    }
  }

  private static final class RecordingHookRuntime implements HookRuntime {
    final List<String> channels = new ArrayList<>();
    final List<Object> values = new ArrayList<>();
    String throwOn;

    @Override
    public void enqueue(String channel, Object value) {
      channels.add(channel);
      values.add(value);
      if (channel.equals(throwOn)) {
        throw new IllegalStateException("expected diagnostic failure");
      }
    }

    @Override
    public boolean quiesceForExtraction(long timeoutMillis) {
      return true;
    }

    @Override
    public void close() {
    }
  }
}
