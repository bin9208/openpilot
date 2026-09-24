package ai.comma.naver.payload;

import android.app.Application;
import java.io.File;
import java.lang.reflect.Method;

final class AndroidApplicationFiles {
  private AndroidApplicationFiles() {
  }

  static File resolve(String directoryName) {
    if (!DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME.equals(directoryName)
        || !DiagnosticConfig.MAIN_PROCESS.equals(AndroidProcess.currentName())) {
      return null;
    }
    Application application = currentApplication();
    if (application == null) {
      return null;
    }
    File filesDir = application.getFilesDir();
    if (filesDir == null) {
      return null;
    }
    File directory = new File(filesDir, directoryName);
    return ensureDirectory(directory);
  }

  static File resolveExport(String directoryName) {
    if (!DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME.equals(directoryName)
        || !DiagnosticConfig.MAIN_PROCESS.equals(AndroidProcess.currentName())) {
      return null;
    }
    Application application = currentApplication();
    if (application == null) {
      return null;
    }
    File externalFiles = application.getExternalFilesDir(null);
    if (externalFiles == null) {
      return null;
    }
    File providerRoot =
        new File(externalFiles, DiagnosticConfig.CAPTURE_EXTERNAL_FILES_ROOT_PATH);
    File directory = new File(providerRoot, directoryName);
    return ensureDirectory(directory);
  }

  private static File ensureDirectory(File directory) {
    return directory.isDirectory() || directory.mkdirs() ? directory : null;
  }

  static Application currentApplication() {
    try {
      Class<?> activityThread = Class.forName("android.app.ActivityThread");
      Method currentApplication = activityThread.getDeclaredMethod("currentApplication");
      currentApplication.setAccessible(true);
      Object application = currentApplication.invoke(null);
      return application instanceof Application ? (Application) application : null;
    } catch (ReflectiveOperationException | RuntimeException ignored) {
      return null;
    }
  }
}
