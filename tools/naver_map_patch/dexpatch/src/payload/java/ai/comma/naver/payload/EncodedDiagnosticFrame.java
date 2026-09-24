package ai.comma.naver.payload;

import java.nio.charset.StandardCharsets;

final class EncodedDiagnosticFrame {
  private final String channel;
  private final long sequence;
  private final long monotonicNs;
  private final long objectDroppedBefore;
  private final String encodedJson;

  EncodedDiagnosticFrame(
      String channel,
      long sequence,
      long monotonicNs,
      long objectDroppedBefore,
      String encodedJson) {
    if (channel == null || encodedJson == null || sequence < 1L
        || objectDroppedBefore < 0L
        || encodedJson.getBytes(StandardCharsets.UTF_8).length
            > DiagnosticConfig.MAX_CHANNEL_CHARS) {
      throw new IllegalArgumentException("invalid encoded diagnostic frame");
    }
    this.channel = channel;
    this.sequence = sequence;
    this.monotonicNs = monotonicNs;
    this.objectDroppedBefore = objectDroppedBefore;
    this.encodedJson = encodedJson;
  }

  String channel() { return channel; }
  long sequence() { return sequence; }
  long monotonicNs() { return monotonicNs; }
  long objectDroppedBefore() { return objectDroppedBefore; }
  String encodedJson() { return encodedJson; }
}
