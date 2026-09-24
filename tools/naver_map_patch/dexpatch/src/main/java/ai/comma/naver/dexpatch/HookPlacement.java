package ai.comma.naver.dexpatch;

public record HookPlacement(Kind kind, int instructionIndex) {
  public enum Kind {
    METHOD_ENTRY,
    BEFORE_INSTRUCTION,
    AFTER_INSTRUCTION
  }

  public HookPlacement {
    if (instructionIndex < 0) {
      throw new IllegalArgumentException("hook placement index must be non-negative");
    }
  }

  public static HookPlacement methodEntry() {
    return new HookPlacement(Kind.METHOD_ENTRY, 0);
  }

  public static HookPlacement beforeInstruction(int index) {
    return new HookPlacement(Kind.BEFORE_INSTRUCTION, index);
  }

  public static HookPlacement afterInstruction(int index) {
    return new HookPlacement(Kind.AFTER_INSTRUCTION, index);
  }
}
