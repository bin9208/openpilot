package ai.comma.naver.payload;

public final class FieldAcceptanceHooks {
  public static final String PAYLOAD_BUILD_ID =
      "naver-6.8.0.5-field-acceptance-v4";
  private static volatile ProductionRuntime productionRuntime =
      new ProductionRuntime(true);
  private static volatile HookRuntime diagnosticRuntime =
      DiagnosticRuntime.createDefault();

  private FieldAcceptanceHooks() {
  }

  public static void onStatus(Object value) {
    try {
      productionRuntime.onStatus(value);
    } catch (RuntimeException ignored) {
    }
    enqueueDiagnostic("status", value);
  }

  public static void onCurrentTbt(Object value) {
    try {
      productionRuntime.onCurrentTbt(value);
    } catch (RuntimeException ignored) {
    }
    enqueueDiagnostic("tbt_current", value);
  }

  public static void onNextTbt(Object value) {
    try {
      productionRuntime.onNextTbt(value);
    } catch (RuntimeException ignored) {
    }
    enqueueDiagnostic("tbt_next", value);
  }

  public static void onSafetySource(Object value) {
    try {
      productionRuntime.onSafetySource(value);
    } catch (RuntimeException ignored) {
    }
    enqueueMappingOutcome("safety");
  }

  public static void onSafety(Object value) {
    try {
      productionRuntime.onSafety(value);
    } catch (RuntimeException ignored) {
    }
    enqueueMappingOutcome("safety");
  }

  public static void onRoute(Object value) {
    try {
      productionRuntime.onRoute(value);
    } catch (RuntimeException ignored) {
    }
    enqueueMappingOutcome("route");
  }

  public static void onLane(Object value) {
    enqueueDiagnostic("lane", value);
  }

  public static void beforeCaptureRead(Object value) {
    if (!AndroidCaptureSharing.isApprovedUri(value)) {
      return;
    }
    if (!diagnosticRuntime.quiesceForExtraction(
        DiagnosticConfig.CAPTURE_QUIESCE_TIMEOUT_MS)) {
      throw new IllegalStateException("diagnostic capture unavailable");
    }
  }

  private static void enqueueDiagnostic(String channel, Object value) {
    try {
      diagnosticRuntime.enqueue(channel, value);
    } catch (RuntimeException ignored) {
    }
  }

  private static void enqueueMappingOutcome(String channel) {
    try {
      Object outcome = productionRuntime.takeMappingOutcome();
      if (outcome != null) {
        diagnosticRuntime.enqueue(channel, outcome);
      }
    } catch (RuntimeException ignored) {
    }
  }

  static void replaceRuntimesForTest(
      ProductionRuntime productionReplacement,
      HookRuntime diagnosticReplacement) {
    ProductionRuntime nextProduction = productionReplacement == null
        ? new ProductionRuntime(true) : productionReplacement;
    HookRuntime nextDiagnostic = diagnosticReplacement == null
        ? DiagnosticRuntime.createDefault() : diagnosticReplacement;
    HookRuntime previousDiagnostic = diagnosticRuntime;
    productionRuntime = nextProduction;
    diagnosticRuntime = nextDiagnostic;
    previousDiagnostic.close();
  }

  static void resetRuntimesForTest() {
    replaceRuntimesForTest(new ProductionRuntime(true), DiagnosticRuntime.createDefault());
  }
}
