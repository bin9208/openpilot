package ai.comma.naver.payload;

import java.io.IOException;
import java.util.List;

final class NetworkFrameConsumer implements FrameBatchConsumer {
  private final NavigationTransport transport;

  NetworkFrameConsumer(NavigationTransport transport) {
    if (transport == null) {
      throw new IllegalArgumentException("network transport is required");
    }
    this.transport = transport;
  }

  @Override
  public void writeBatch(List<EncodedDiagnosticFrame> frames, DiagnosticCounters.Snapshot counters)
      throws IOException {
    for (EncodedDiagnosticFrame frame : frames) {
      transport.send(frame.encodedJson());
    }
  }

  @Override
  public void close() {
    transport.close();
  }
}
