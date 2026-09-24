package ai.comma.naver.payload;

import java.io.IOException;

interface CaptureDatabase extends AutoCloseable {
  void beginTransaction() throws IOException;
  void ensureRun(String runId, long startedMonotonicNs, String buildId) throws IOException;
  void markRunClean(String runId) throws IOException;
  long countRows(String channel) throws IOException;
  long countPinnedRows(String channel) throws IOException;
  void insert(String runId, EncodedDiagnosticFrame frame, boolean pinned) throws IOException;
  void deleteOldestUnpinned(String channel, long count) throws IOException;
  void putMeta(String key, String value) throws IOException;
  void setTransactionSuccessful() throws IOException;
  void endTransaction() throws IOException;

  @Override
  void close();
}
