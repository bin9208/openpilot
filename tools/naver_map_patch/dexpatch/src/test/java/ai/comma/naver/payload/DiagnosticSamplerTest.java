package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import org.junit.jupiter.api.Test;

final class DiagnosticSamplerTest {
  enum UnicodeEnum {
    안내
  }

  static final class UnicodeMethodProbe {
    public int 안내() {
      return 1;
    }
  }

  static final class Probe {
    int calls;

    public String allowedState() {
      calls += 1;
      return "ACTIVE";
    }

    public double latitude() {
      throw new AssertionError("sensitive accessor must not be called");
    }

    public String homeAddress() {
      throw new AssertionError("identity accessor must not be called");
    }

    public String arbitraryValue() {
      throw new AssertionError("non-allowlisted accessor must not be called");
    }

    @Override
    public String toString() {
      throw new AssertionError("arbitrary toString must not be called");
    }
  }

  public static final class NestedProbe {
    private final Object child;

    NestedProbe(Object child) {
      this.child = child;
    }

    public Object next() {
      return child;
    }
  }

  public static final class LateAllowlistedProbe {
    int calls;

    public int a00() { return 0; }
    public int a01() { return 1; }
    public int a02() { return 2; }
    public int a03() { return 3; }
    public int a04() { return 4; }
    public int a05() { return 5; }
    public int a06() { return 6; }
    public int a07() { return 7; }
    public int a08() { return 8; }
    public int a09() { return 9; }
    public int a10() { return 10; }
    public int a11() { return 11; }
    public int a12() { return 12; }
    public int a13() { return 13; }
    public int a14() { return 14; }
    public int a15() { return 15; }

    public String zAllowed() {
      calls += 1;
      return "included";
    }
  }

  @Test
  void invokesOnlyExplicitlyAllowlistedAccessor() {
    Probe probe = new Probe();
    DiagnosticSampler sampler = new DiagnosticSampler(
        new HashSet<>(Collections.singletonList(
            Probe.class.getName() + "#allowedState()Ljava/lang/String;")),
        4, 16, 4, 160, 4096);

    String sample = sampler.sample(probe);

    assertEquals(1, probe.calls);
    assertTrue(sample.contains("allowedState"));
    assertTrue(sample.contains("Ljava/lang/String;"));
    assertTrue(sample.contains("\"string_length\":6"));
    assertFalse(sample.contains("ACTIVE"));
    assertFalse(sample.toLowerCase().contains("latitude"));
    assertFalse(sample.toLowerCase().contains("homeaddress"));
  }

  @Test
  void boundsDepthAccessorsCollectionsStringsAndChannel() {
    DiagnosticSampler sampler = new DiagnosticSampler(
        Collections.<String>emptySet(), 4, 16, 4, 160, 4096);
    Object value = Arrays.asList(
        "a", "b", "c", "d", "must-not-be-sampled",
        Arrays.asList(Arrays.asList(Arrays.asList("too-deep"))));

    String sample = sampler.sample(value);

    assertTrue(sample.length() <= 4096);
    assertTrue(sample.contains("\"collection_size\":6"));
    assertTrue(sample.contains("\"sampled_items\":4"));
    assertFalse(sample.contains("must-not-be-sampled"));
    assertFalse(sample.contains("too-deep"));
  }

  @Test
  void emitsEnumNameAndBucketedNumbersWithoutPreciseValues() {
    DiagnosticSampler sampler = new DiagnosticSampler(
        Collections.<String>emptySet(), 4, 16, 4, 160, 4096);

    String enumSample = sampler.sample(Thread.State.RUNNABLE);
    String numberSample = sampler.sample(Double.valueOf(37.566535));

    assertTrue(enumSample.contains("RUNNABLE"));
    assertTrue(numberSample.contains("\"numeric_bucket\""));
    assertFalse(numberSample.contains("37.566535"));
  }

  @Test
  void rejectsNonAsciiEnumNamesMethodNamesAndClassDescriptors() {
    DiagnosticSampler sampler = new DiagnosticSampler(
        Collections.<String>emptySet(), 4, 16, 4, 160, 4096);

    assertThrows(IllegalArgumentException.class, () -> sampler.sample(UnicodeEnum.안내));
    assertThrows(IllegalArgumentException.class, () -> sampler.sample(new UnicodeMethodProbe()));
  }

  @Test
  void followsAllowlistedAccessorsThroughDepthFourOnly() {
    NestedProbe root = new NestedProbe(
        new NestedProbe(new NestedProbe(new NestedProbe(new NestedProbe("too-deep")))));
    String key = NestedProbe.class.getName() + "#next()Ljava/lang/Object;";
    DiagnosticSampler sampler = new DiagnosticSampler(
        new HashSet<>(Collections.singletonList(key)), 4, 16, 4, 160, 4096);

    String sample = sampler.sample(root);

    assertTrue(sample.contains("depth_limited"));
    assertFalse(sample.contains("too-deep"));
  }

  @Test
  void prioritizesAllowlistedAccessorBeyondFirstSixteenSortedCandidates() {
    LateAllowlistedProbe probe = new LateAllowlistedProbe();
    DiagnosticSampler sampler = new DiagnosticSampler(new HashSet<>(Collections.singletonList(
        LateAllowlistedProbe.class.getName() + "#zAllowed()Ljava/lang/String;")),
        4, 16, 4, 160, 4096);

    String sample = sampler.sample(probe);

    assertEquals(1, probe.calls);
    assertTrue(sample.contains("\"name\":\"zAllowed\""));
    assertTrue(sample.contains("\"name\":\"a00\""));
    assertFalse(sample.contains("\"name\":\"a15\""));
    assertEquals(16, sample.split("\\\"name\\\":", -1).length - 1);
  }
}
