package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

final class EncodedDiagnosticFrameTest {
  @Test
  void encoderReturnsOneImmutableFrameWithMatchingEnvelopeFields() {
    EncodedDiagnosticFrame frame =
        new BoundedMappingDiagnostics(4096).encode("status", Boolean.TRUE, 3);

    assertEquals("status", frame.channel());
    assertEquals(1L, frame.sequence());
    assertEquals(3L, frame.objectDroppedBefore());
    assertTrue(frame.encodedJson().contains(
        "\"monotonic_ns\":" + frame.monotonicNs()));
    assertTrue(frame.encodedJson().contains("\"sequence\":1"));
    assertTrue(frame.encodedJson().contains("\"dropped\":3"));
  }

  @Test
  void unknownChannelFailsBeforeAFrameCanBePersisted() {
    assertThrows(IllegalArgumentException.class,
        () -> new BoundedMappingDiagnostics(4096)
            .encode("unknown", Boolean.TRUE, 0));
  }

  @Test
  void encodedFrameBoundIsMeasuredInUtf8Bytes() {
    String exactAsciiBoundary = "a".repeat(4096);
    assertEquals(
        exactAsciiBoundary,
        new EncodedDiagnosticFrame("status", 1L, 1L, 0L, exactAsciiBoundary).encodedJson());

    String oversizedUnicode = "가".repeat(1366);
    assertTrue(oversizedUnicode.length() < 4096);
    assertThrows(
        IllegalArgumentException.class,
        () -> new EncodedDiagnosticFrame("status", 1L, 1L, 0L, oversizedUnicode));
  }
}
