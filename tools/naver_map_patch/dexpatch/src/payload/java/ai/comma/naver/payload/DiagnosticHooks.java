package ai.comma.naver.payload;

public final class DiagnosticHooks {
  private static volatile HookRuntime runtime = DiagnosticRuntime.createDefault();

  private DiagnosticHooks() {
  }

  public static void onStatus(Object value) {
    runtime.enqueue("status", value);
  }

  public static void onCurrentTbt(Object value) {
    runtime.enqueue("tbt_current", value);
  }

  public static void onNextTbt(Object value) {
    runtime.enqueue("tbt_next", value);
  }

  public static void onSafetySource(Object value) {
    runtime.enqueue("safety", value);
  }

  public static void onSafety(Object value) {
    runtime.enqueue("safety", value);
  }

  public static void onRoute(Object value) {
    runtime.enqueue("route", value);
  }

  public static void onLane(Object value) {
    runtime.enqueue("lane", value);
  }

  public static void beforeCaptureRead(Object value) {
    if (!AndroidCaptureSharing.isApprovedUri(value)) {
      return;
    }
    if (!runtime.quiesceForExtraction(
        DiagnosticConfig.CAPTURE_QUIESCE_TIMEOUT_MS)) {
      throw new IllegalStateException("diagnostic capture unavailable");
    }
  }

  static void replaceRuntimeForTest(HookRuntime replacement) {
    HookRuntime previous = runtime;
    runtime = replacement;
    previous.close();
  }
}
