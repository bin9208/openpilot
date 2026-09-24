package ai.comma.naver.payload;

import android.app.Application;
import android.content.Intent;
import android.net.Uri;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;

final class AndroidCaptureSharing {
  private static final int COPY_BUFFER_BYTES = 64 * 1024;
  private static final String SNAPSHOT_TEMP_SUFFIX = ".snapshot.tmp";

  private AndroidCaptureSharing() {
  }

  @FunctionalInterface
  interface SnapshotMover {
    void move(File temporary, File destination) throws IOException;
  }

  static boolean grantReadAccess() {
    Application application = AndroidApplicationFiles.currentApplication();
    if (application == null) {
      return false;
    }
    try {
      for (String value : approvedUriStrings()) {
        application.grantUriPermission(
            DiagnosticConfig.CAPTURE_GRANTEE_PACKAGE,
            Uri.parse(value),
            Intent.FLAG_GRANT_READ_URI_PERMISSION);
      }
      return true;
    } catch (RuntimeException ignored) {
      return false;
    }
  }

  static boolean isApprovedUri(Object value) {
    return value instanceof Uri && isApprovedUriString(value.toString());
  }

  static boolean isApprovedUriString(String value) {
    if (value == null) {
      return false;
    }
    for (String allowed : approvedUriStrings()) {
      if (allowed.equals(value)) {
        return true;
      }
    }
    return false;
  }

  static String[] approvedUriStrings() {
    String prefix = "content://" + DiagnosticConfig.CAPTURE_AUTHORITY + "/"
        + DiagnosticConfig.CAPTURE_FILE_PROVIDER_ROOT_NAME + "/"
        + DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME + "/";
    return new String[] {
        prefix + DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME,
    };
  }

  static boolean publishSnapshot(File canonicalDatabase, long deadlineNanos) {
    File exportDirectory =
        AndroidApplicationFiles.resolveExport(DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME);
    if (exportDirectory == null) {
      return false;
    }
    File exportedDatabase =
        new File(exportDirectory, DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME);
    try {
      publishSnapshot(canonicalDatabase, exportedDatabase, deadlineNanos);
      return true;
    } catch (IOException | RuntimeException ignored) {
      return false;
    }
  }

  static void publishSnapshot(
      File canonicalDatabase, File exportedDatabase, long deadlineNanos) throws IOException {
    publishSnapshot(
        canonicalDatabase,
        exportedDatabase,
        deadlineNanos,
        AndroidCaptureSharing::replacePublishedFile);
  }

  static void publishSnapshot(
      File canonicalDatabase,
      File exportedDatabase,
      long deadlineNanos,
      SnapshotMover snapshotMover) throws IOException {
    validateSnapshotPaths(canonicalDatabase, exportedDatabase);
    if (snapshotMover == null) {
      throw new IOException("capture snapshot mover unavailable");
    }
    checkDeadline(deadlineNanos);

    long expectedBytes = canonicalDatabase.length();
    if (expectedBytes > DiagnosticConfig.OFFLINE_CAPTURE_MAX_DATABASE_BYTES) {
      throw new IOException("capture snapshot exceeds size limit");
    }

    File temporary = new File(
        exportedDatabase.getParentFile(),
        DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME + SNAPSHOT_TEMP_SUFFIX);
    boolean published = false;
    try {
      Files.deleteIfExists(temporary.toPath());
      copyBounded(canonicalDatabase, temporary, expectedBytes, deadlineNanos);
      checkDeadline(deadlineNanos);
      snapshotMover.move(temporary, exportedDatabase);
      published = true;
    } finally {
      if (!published) {
        Files.deleteIfExists(temporary.toPath());
      }
    }
  }

  private static void validateSnapshotPaths(File canonicalDatabase, File exportedDatabase)
      throws IOException {
    if (canonicalDatabase == null || exportedDatabase == null
        || !canonicalDatabase.isFile()
        || !isCanonicalDatabaseName(canonicalDatabase.getName())
        || canonicalDatabase.getParentFile() == null
        || !DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME.equals(
            canonicalDatabase.getParentFile().getName())
        || !DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME.equals(
            exportedDatabase.getName())
        || exportedDatabase.getParentFile() == null
        || !DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME.equals(
            exportedDatabase.getParentFile().getName())
        || exportedDatabase.getParentFile().getParentFile() == null
        || !DiagnosticConfig.CAPTURE_EXTERNAL_FILES_ROOT_PATH.equals(
            exportedDatabase.getParentFile().getParentFile().getName())
        || canonicalDatabase.getCanonicalFile().equals(exportedDatabase.getCanonicalFile())) {
      throw new IOException("invalid capture snapshot path");
    }
  }

  private static boolean isCanonicalDatabaseName(String name) {
    return DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_NAME.equals(name)
        || DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_RECOVERY_NAME.equals(name);
  }

  private static void copyBounded(
      File source, File temporary, long expectedBytes, long deadlineNanos) throws IOException {
    long copiedBytes = 0L;
    byte[] buffer = new byte[COPY_BUFFER_BYTES];
    try (FileInputStream input = new FileInputStream(source);
         FileOutputStream output = new FileOutputStream(temporary)) {
      while (true) {
        checkDeadline(deadlineNanos);
        int count = input.read(buffer);
        if (count < 0) {
          break;
        }
        copiedBytes += count;
        if (copiedBytes > DiagnosticConfig.OFFLINE_CAPTURE_MAX_DATABASE_BYTES) {
          throw new IOException("capture snapshot exceeds size limit");
        }
        output.write(buffer, 0, count);
      }
      if (copiedBytes != expectedBytes || source.length() != expectedBytes) {
        throw new IOException("capture snapshot source changed");
      }
      output.flush();
      output.getFD().sync();
    }
  }

  private static void replacePublishedFile(File temporary, File exportedDatabase)
      throws IOException {
    Files.move(
        temporary.toPath(),
        exportedDatabase.toPath(),
        StandardCopyOption.ATOMIC_MOVE,
        StandardCopyOption.REPLACE_EXISTING);
  }

  private static void checkDeadline(long deadlineNanos) throws IOException {
    if (deadlineNanos <= 0L || System.nanoTime() >= deadlineNanos) {
      throw new IOException("capture snapshot deadline expired");
    }
  }
}
