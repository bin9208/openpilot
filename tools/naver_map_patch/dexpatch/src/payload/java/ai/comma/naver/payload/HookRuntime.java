package ai.comma.naver.payload;

interface HookRuntime extends AutoCloseable {
  void enqueue(String channel, Object value);

  boolean quiesceForExtraction(long timeoutMillis);

  @Override
  void close();
}
