package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

final class AndroidCaptureSnapshotTest {
  @TempDir
  Path temporaryDirectory;

  @Test
  void atomicallyPublishesOneSelfContainedV2DatabaseAndLeavesV1ExportsUntouched()
      throws Exception {
    Path canonicalDirectory = canonicalDirectory();
    Path exportDirectory = exportDirectory();
    Path canonical = canonicalDirectory.resolve(
        DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_NAME);
    byte[] database = new byte[] {1, 2, 3, 4};
    Files.write(canonical, database);
    Path legacy = exportDirectory.resolve("capture-v1.sqlite3");
    byte[] legacyBytes = new byte[] {8, 8};
    Files.write(legacy, legacyBytes);
    Path exported = exportDirectory.resolve(
        DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME);

    AndroidCaptureSharing.publishSnapshot(
        canonical.toFile(), exported.toFile(), deadline());

    assertArrayEquals(database, Files.readAllBytes(exported));
    assertArrayEquals(database, Files.readAllBytes(canonical));
    assertArrayEquals(legacyBytes, Files.readAllBytes(legacy));
    assertEquals(
        Set.of(
            "capture-v1.sqlite3",
            DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME),
        fileNames(exportDirectory));
  }

  @Test
  void recoveryDatabaseCanBeTheSingleSnapshotSource() throws Exception {
    Path canonicalDirectory = canonicalDirectory();
    Path canonical = canonicalDirectory.resolve(
        DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_RECOVERY_NAME);
    byte[] database = new byte[] {5, 6, 7};
    Files.write(canonical, database);
    Path exported = exportDirectory().resolve(
        DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME);

    AndroidCaptureSharing.publishSnapshot(
        canonical.toFile(), exported.toFile(), deadline());

    assertArrayEquals(database, Files.readAllBytes(exported));
    assertArrayEquals(database, Files.readAllBytes(canonical));
  }

  @Test
  void oversizedCanonicalFileFailsBeforeReplacingPublishedSnapshot() throws Exception {
    Path canonical = canonicalDirectory().resolve(
        DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_NAME);
    try (RandomAccessFile file = new RandomAccessFile(canonical.toFile(), "rw")) {
      file.setLength(DiagnosticConfig.OFFLINE_CAPTURE_MAX_DATABASE_BYTES + 1L);
    }
    Path exported = exportDirectory().resolve(
        DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME);
    byte[] previous = new byte[] {7, 7, 7};
    Files.write(exported, previous);

    assertThrows(
        IOException.class,
        () -> AndroidCaptureSharing.publishSnapshot(
            canonical.toFile(), exported.toFile(), deadline()));

    assertArrayEquals(previous, Files.readAllBytes(exported));
    assertTrue(Files.isRegularFile(canonical));
    assertEquals(
        Set.of(DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME),
        fileNames(exported.getParent()));
  }

  @Test
  void expiredDeadlineFailsBeforeReplacingPublishedSnapshot() throws Exception {
    Path canonical = canonicalDirectory().resolve(
        DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_NAME);
    Files.write(canonical, new byte[] {1});
    Path exported = exportDirectory().resolve(
        DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME);
    byte[] previous = new byte[] {2};
    Files.write(exported, previous);

    assertThrows(
        IOException.class,
        () -> AndroidCaptureSharing.publishSnapshot(
            canonical.toFile(), exported.toFile(), System.nanoTime() - 1L));

    assertArrayEquals(previous, Files.readAllBytes(exported));
  }

  @Test
  void unsupportedAtomicMoveFailsClosedAndPreservesPublishedSnapshot() throws Exception {
    Path canonical = canonicalDirectory().resolve(
        DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_NAME);
    Files.write(canonical, new byte[] {1, 2, 3});
    Path exported = exportDirectory().resolve(
        DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME);
    byte[] previous = new byte[] {9, 9, 9};
    Files.write(exported, previous);

    assertThrows(
        AtomicMoveNotSupportedException.class,
        () -> AndroidCaptureSharing.publishSnapshot(
            canonical.toFile(),
            exported.toFile(),
            deadline(),
            (temporary, destination) -> {
              throw new AtomicMoveNotSupportedException(
                  temporary.toString(), destination.toString(), "unsupported");
            }));

    assertArrayEquals(previous, Files.readAllBytes(exported));
    assertArrayEquals(new byte[] {1, 2, 3}, Files.readAllBytes(canonical));
    assertEquals(
        Set.of(DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME),
        fileNames(exported.getParent()));
  }

  @Test
  void rejectsSourceAndDestinationOutsideTheFixedLayout() throws Exception {
    Path wrongCanonical = Files.createDirectories(
        temporaryDirectory.resolve("internal/wrong")).resolve(
            DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_NAME);
    Files.write(wrongCanonical, new byte[] {1});
    Path wrongExport = Files.createDirectories(
        temporaryDirectory.resolve("external/wrong-root")
            .resolve(DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME))
        .resolve(DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME);

    assertThrows(
        IOException.class,
        () -> AndroidCaptureSharing.publishSnapshot(
            wrongCanonical.toFile(), wrongExport.toFile(), deadline()));
    assertFalse(Files.exists(wrongExport));
    assertArrayEquals(new byte[] {1}, Files.readAllBytes(wrongCanonical));
  }

  private Path canonicalDirectory() throws IOException {
    return Files.createDirectories(
        temporaryDirectory.resolve("internal")
            .resolve(DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME));
  }

  private Path exportDirectory() throws IOException {
    return Files.createDirectories(
        temporaryDirectory.resolve("external")
            .resolve(DiagnosticConfig.CAPTURE_EXTERNAL_FILES_ROOT_PATH)
            .resolve(DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME));
  }

  private static Set<String> fileNames(Path directory) throws IOException {
    try (Stream<Path> paths = Files.list(directory)) {
      return paths.map(path -> path.getFileName().toString()).collect(Collectors.toSet());
    }
  }

  private static long deadline() {
    return System.nanoTime()
        + TimeUnit.MILLISECONDS.toNanos(DiagnosticConfig.CAPTURE_QUIESCE_TIMEOUT_MS);
  }
}
