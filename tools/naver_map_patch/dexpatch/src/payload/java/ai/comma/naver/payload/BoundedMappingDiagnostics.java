package ai.comma.naver.payload;

import java.util.Arrays;
import java.util.HashSet;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicLong;

final class BoundedMappingDiagnostics {
  private final AtomicLong sequence = new AtomicLong();
  private final int maxChannelChars;

  BoundedMappingDiagnostics(int maxChannelChars) {
    this.maxChannelChars = maxChannelChars;
  }

  EncodedDiagnosticFrame encode(String channel, Object value, long dropped) {
    if (!isKnownChannel(channel)) {
      throw new IllegalArgumentException("unknown diagnostic channel");
    }
    if (!DiagnosticSampler.acceptsChannelValue(channel, value)) {
      throw new IllegalArgumentException("field mapping outcome is required");
    }
    DiagnosticSampler sampler = new DiagnosticSampler(
        new HashSet<>(Arrays.asList(DiagnosticConfig.accessorAllowlist(channel))),
        DiagnosticConfig.MAX_DEPTH,
        DiagnosticConfig.MAX_ACCESSORS,
        DiagnosticConfig.MAX_COLLECTION_ITEMS,
        DiagnosticConfig.MAX_SAMPLE_CHARS,
        DiagnosticConfig.MAX_CHANNEL_CHARS);
    String observation = sampler.sample(value);
    long nextSequence = sequence.incrementAndGet();
    long monotonic = System.nanoTime();
    String prefix = "{\"schema\":\"naver.diagnostic.v1\",\"channel\":\""
        + channel
        + "\",\"sequence\":"
        + nextSequence
        + ",\"monotonic_ns\":"
        + monotonic
        + ",\"dropped\":"
        + dropped
        + ",\"observation\":";
    String line = prefix + observation + "}";
    if (line.getBytes(StandardCharsets.UTF_8).length > maxChannelChars) {
      line = prefix + "{\"truncated\":true}}";
    }
    return new EncodedDiagnosticFrame(channel, nextSequence, monotonic, dropped, line);
  }

  private static boolean isKnownChannel(String channel) {
    return "status".equals(channel)
        || "tbt_current".equals(channel)
        || "tbt_next".equals(channel)
        || "safety".equals(channel)
        || "route".equals(channel)
        || "lane".equals(channel);
  }
}
