package ai.comma.naver.dexpatch;

public record HookArgument(Kind kind, int index) {
  public enum Kind {
    PARAMETER,
    WINDOW_REGISTER
  }

  public HookArgument {
    if (index < 0) {
      throw new IllegalArgumentException("hook argument index must be non-negative");
    }
  }

  public static HookArgument parameter(int index) {
    return new HookArgument(Kind.PARAMETER, index);
  }

  public static HookArgument windowRegister(int index) {
    return new HookArgument(Kind.WINDOW_REGISTER, index);
  }
}
