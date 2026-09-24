package android.util;

import java.util.ArrayList;
import java.util.List;

public final class Log {
  private static final List<String> ENTRIES = new ArrayList<>();

  private Log() {
  }

  public static synchronized int w(String tag, String message) {
    ENTRIES.add(tag + " " + message);
    return 0;
  }

  public static synchronized void reset() {
    ENTRIES.clear();
  }

  public static synchronized List<String> entries() {
    return List.copyOf(ENTRIES);
  }
}
