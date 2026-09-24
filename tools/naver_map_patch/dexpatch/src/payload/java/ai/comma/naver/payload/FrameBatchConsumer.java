package ai.comma.naver.payload;

import java.io.IOException;
import java.util.List;

interface FrameBatchConsumer extends AutoCloseable {
  void writeBatch(List<EncodedDiagnosticFrame> frames, DiagnosticCounters.Snapshot counters)
      throws IOException;

  @Override
  void close();
}
