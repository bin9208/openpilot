package ai.comma.naver.payload;

import java.lang.reflect.Method;

final class AndroidProcess {
  private AndroidProcess() {
  }

  static String currentName() {
    String name = invokeStaticString("android.app.Application", "getProcessName");
    if (name != null) {
      return name;
    }
    return invokeStaticString("android.app.ActivityThread", "currentProcessName");
  }

  static int currentPid() {
    try {
      Class<?> process = Class.forName("android.os.Process");
      Method method = process.getMethod("myPid");
      Object value = method.invoke(null);
      return value instanceof Integer ? ((Integer) value).intValue() : -1;
    } catch (ReflectiveOperationException | RuntimeException ignored) {
      return -1;
    }
  }

  private static String invokeStaticString(String className, String methodName) {
    try {
      Class<?> owner = Class.forName(className);
      Method method = owner.getMethod(methodName);
      Object value = method.invoke(null);
      return value instanceof String ? (String) value : null;
    } catch (ReflectiveOperationException | RuntimeException ignored) {
      return null;
    }
  }
}
