package ai.comma.naver.payload;

interface DiagnosticFrameSink {
  boolean offer(EncodedDiagnosticFrame frame);

  default boolean quiesceAndClose(long timeoutMillis) {
    drainAndClose(timeoutMillis);
    return true;
  }

  void drainAndClose(long timeoutMillis);
}
