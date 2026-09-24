package ai.comma.naver.payload;

public final class ProductionHooks {
  public static final String PAYLOAD_BUILD_ID = "naver-6.8.0.5-public-beta-v2";
  private static volatile ProductionRuntime runtime = new ProductionRuntime();

  private ProductionHooks() {
  }

  public static void onStatus(Object status) {
    runtime.onStatus(status);
  }

  public static void onCurrentTbt(Object item) {
    runtime.onCurrentTbt(item);
  }

  public static void onNextTbt(Object item) {
    runtime.onNextTbt(item);
  }

  public static void onRoute(Object route) {
    runtime.onRoute(route);
  }

  public static void onSafetySource(Object item) {
    runtime.onSafetySource(item);
  }

  public static void onSafety(Object item) {
    runtime.onSafety(item);
  }

  public static void setRuntimeForTesting(ProductionRuntime replacement) {
    runtime = replacement == null ? new ProductionRuntime() : replacement;
  }

  public static void resetRuntimeForTesting() {
    runtime = new ProductionRuntime();
  }
}
