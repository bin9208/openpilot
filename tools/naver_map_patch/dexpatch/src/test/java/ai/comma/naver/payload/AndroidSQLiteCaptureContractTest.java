package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;

final class AndroidSQLiteCaptureContractTest {
  private static final Path PAYLOAD_SOURCE_DIRECTORY =
      Path.of("src/payload/java/ai/comma/naver/payload");

  @Test
  void androidStorageAndSqliteImportsAreConfinedToTheThinAdapters() throws Exception {
    assertSourceImports("AndroidApplicationFiles.java", "android.app.Application");
    assertSourceImports("AndroidCaptureSharing.java", "android.content.Intent");
    assertSourceImports("AndroidCaptureSharing.java", "android.net.Uri");
    assertSourceImports(
        "AndroidSQLiteCaptureDatabase.java", "android.database.sqlite.SQLiteDatabase");
    assertNoOtherPayloadSourceImports("android.");
  }

  @Test
  void captureSharingGrantsOnlyTheFixedV2ReadOnlyProviderUriToShell() throws Exception {
    String source = readSource("AndroidCaptureSharing.java");

    assertTrue(source.contains("DiagnosticConfig.CAPTURE_AUTHORITY"));
    assertTrue(source.contains("DiagnosticConfig.CAPTURE_FILE_PROVIDER_ROOT_NAME"));
    assertTrue(source.contains("DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME"));
    assertTrue(source.contains("DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME"));
    assertTrue(source.contains("DiagnosticConfig.CAPTURE_GRANTEE_PACKAGE"));
    assertTrue(source.contains("Intent.FLAG_GRANT_READ_URI_PERMISSION"));
    assertTrue(source.contains("application.grantUriPermission("));
    assertFalse(source.contains("FLAG_GRANT_WRITE_URI_PERMISSION"));
    assertFalse(source.contains("FLAG_GRANT_PREFIX_URI_PERMISSION"));
    assertFalse(source.contains("FLAG_GRANT_PERSISTABLE_URI_PERMISSION"));
    assertFalse(source.contains("setReadable("));

    String[] uris = AndroidCaptureSharing.approvedUriStrings();
    assertEquals(1, uris.length);
    assertEquals(
        "content://com.nhn.android.nmap.fileprovider/navi_trace/naver-diagnostic/"
            + DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME,
        uris[0]);
    assertTrue(AndroidCaptureSharing.isApprovedUriString(uris[0]));
    assertFalse(AndroidCaptureSharing.isApprovedUriString(
        uris[0] + "/unexpected"));
    assertFalse(AndroidCaptureSharing.isApprovedUriString(
        "content://com.nhn.android.nmap.fileprovider/navi_trace/naver-diagnostic/"
            + DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_NAME));
    assertFalse(AndroidCaptureSharing.isApprovedUriString(
        "content://com.nhn.android.nmap.fileprovider/navi_trace/naver-diagnostic/"
            + DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_RECOVERY_NAME));
    assertFalse(AndroidCaptureSharing.isApprovedUriString(
        "content://com.nhn.android.nmap.fileprovider/navi_trace/naver-diagnostic/"
            + "capture-v1.sqlite3"));
    assertFalse(AndroidCaptureSharing.isApprovedUriString(
        "content://com.nhn.android.nmap.fileprovider/navi_trace/naver-diagnostic/"
            + "capture-v1-recovery.sqlite3-wal"));
    assertFalse(AndroidCaptureSharing.isApprovedUriString(
        "content://com.nhn.android.nmap.fileprovider/navi_trace/other.sqlite3"));
  }

  @Test
  void fileProviderHookChecksExactUriBeforeSynchronousQuiesce() throws Exception {
    String source = readSource("DiagnosticHooks.java");
    String hook = methodSlice(
        source, "public static void beforeCaptureRead(Object value)",
        "static void replaceRuntimeForTest(");

    assertOrdered(
        hook,
        "AndroidCaptureSharing.isApprovedUri(value)",
        "runtime.quiesceForExtraction(",
        "DiagnosticConfig.CAPTURE_QUIESCE_TIMEOUT_MS",
        "throw new IllegalStateException(");
    assertFalse(hook.contains("publishSnapshot("));
  }

  @Test
  void applicationFilesSeparatesInternalCanonicalAndProviderBackedExportDirectories()
      throws Exception {
    String source = readSource("AndroidApplicationFiles.java");

    assertTrue(source.contains("android.app.ActivityThread"));
    assertTrue(source.contains("currentApplication"));
    assertTrue(source.contains("DiagnosticConfig.MAIN_PROCESS"));
    assertTrue(source.contains("application.getFilesDir()"));
    assertTrue(source.contains("application.getExternalFilesDir(null)"));
    assertTrue(source.contains("DiagnosticConfig.CAPTURE_EXTERNAL_FILES_ROOT_PATH"));
    assertTrue(source.contains("DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME"));
    assertTrue(source.contains("new File(filesDir, directoryName)"));
    assertTrue(source.contains("new File(providerRoot"));
    assertFalse(source.contains("getExternalStorageDirectory"));
  }

  @Test
  void sqliteAdapterContainsTheExactSchemaAndIndexContract() throws Exception {
    String source = readSource("AndroidSQLiteCaptureDatabase.java");

    assertEquals(normalizeWhitespace("""
        CREATE TABLE capture_meta (
          key TEXT PRIMARY KEY NOT NULL,
          value TEXT NOT NULL
        );
        """), normalizeWhitespace(stringConstant(source, "CREATE_META_TABLE_SQL")));
    assertEquals(normalizeWhitespace("""
        CREATE TABLE capture_runs (
          run_id TEXT PRIMARY KEY NOT NULL,
          started_monotonic_ns INTEGER NOT NULL,
          payload_build_id TEXT NOT NULL,
          clean_shutdown INTEGER NOT NULL DEFAULT 0
            CHECK(clean_shutdown IN (0, 1))
        );
        """), normalizeWhitespace(stringConstant(source, "CREATE_RUNS_TABLE_SQL")));
    assertEquals(normalizeWhitespace("""
        CREATE TABLE capture_frames (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT NOT NULL,
          sequence INTEGER NOT NULL CHECK(sequence >= 1),
          monotonic_ns INTEGER NOT NULL,
          channel TEXT NOT NULL CHECK(channel IN (
            'status', 'tbt_current', 'tbt_next', 'safety', 'route', 'lane'
          )),
          object_dropped_before INTEGER NOT NULL CHECK(object_dropped_before >= 0),
          encoded_json TEXT NOT NULL,
          pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0, 1))
        );
        """), normalizeWhitespace(stringConstant(source, "CREATE_FRAMES_TABLE_SQL")));
    assertEquals(normalizeWhitespace("""
        CREATE INDEX capture_frames_channel_id
        ON capture_frames(channel, id);
        """), normalizeWhitespace(stringConstant(source, "CREATE_FRAMES_INDEX_SQL")));
  }

  @Test
  void sqliteAdapterContainsTheApprovedPragmasAndMetadataKeys() throws Exception {
    String source = readSource("AndroidSQLiteCaptureDatabase.java");

    assertTrue(source.contains("\"PRAGMA user_version=\""));
    assertTrue(source.contains("DiagnosticConfig.OFFLINE_CAPTURE_SCHEMA_VERSION"));
    assertFalse(source.contains("\"PRAGMA user_version=1;\""));
    assertEquals("PRAGMA journal_mode=WAL;", stringConstant(source, "SET_JOURNAL_MODE_SQL"));
    assertEquals("PRAGMA synchronous=NORMAL;", stringConstant(source, "SET_SYNCHRONOUS_SQL"));
    assertTrue(source.contains("\"PRAGMA wal_autocheckpoint=\""));
    assertTrue(source.contains("DiagnosticConfig.OFFLINE_CAPTURE_WAL_AUTOCHECKPOINT_PAGES"));
    assertEquals(
        "PRAGMA wal_checkpoint(TRUNCATE);",
        stringConstant(source, "CHECKPOINT_FOR_EXTRACTION_SQL"));
    assertTrue(source.contains("\"PRAGMA journal_size_limit=\""));
    assertTrue(source.contains("DiagnosticConfig.OFFLINE_CAPTURE_JOURNAL_SIZE_LIMIT_BYTES"));
    assertFalse(source.contains("\"PRAGMA journal_size_limit=4194304;\""));
    assertTrue(source.contains(
        "long journalSizeLimit = queryPragmaLong(database, SET_JOURNAL_SIZE_LIMIT_SQL);"));
    assertTrue(source.contains(
        "journalSizeLimit != DiagnosticConfig.OFFLINE_CAPTURE_JOURNAL_SIZE_LIMIT_BYTES"));
    assertFalse(source.contains("database.execSQL(SET_JOURNAL_SIZE_LIMIT_SQL)"));
    assertEquals("PRAGMA page_size;", stringConstant(source, "READ_PAGE_SIZE_SQL"));
    assertTrue(source.contains("PRAGMA max_page_count="));
    assertFalse(source.contains("\"PRAGMA max_page_count=\" + maximumPages"));
    assertTrue(source.contains("DiagnosticConfig.OFFLINE_CAPTURE_MAX_DATABASE_BYTES"));
    assertTrue(source.contains("appliedMaximumPages != maximumPages"));
    assertTrue(source.contains("schema_version"));
    assertTrue(source.contains("payload_build_id"));
    assertTrue(source.contains("object_queue_dropped"));
    assertTrue(source.contains("local_queue_dropped"));
    assertTrue(source.contains("network_queue_dropped"));
    assertTrue(source.contains("storage_errors"));
    assertTrue(source.contains("network_errors"));
    assertTrue(source.contains("retention_deletes"));
    assertTrue(source.contains("rejected_frames"));
  }

  @Test
  void incompatibleDatabasesUseOnlyTheTwoApprovedNamesWithoutReplacement() throws Exception {
    String source = readSource("AndroidSQLiteCaptureDatabase.java");

    assertTrue(source.contains("DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_NAME"));
    assertTrue(source.contains("DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_RECOVERY_NAME"));
    assertTrue(source.contains("closeQuietly(opened)"));
    assertFalse(source.contains(".delete("));
    assertFalse(source.contains(".renameTo("));
  }

  @Test
  void everyDatabaseOpenUsesOneExplicitNonDestructiveCorruptionHandler() throws Exception {
    String source = readSource("AndroidSQLiteCaptureDatabase.java");

    assertSourceImports(
        "AndroidSQLiteCaptureDatabase.java", "android.database.DatabaseErrorHandler");
    assertEquals(1, countOccurrences(source, "SQLiteDatabase.openDatabase("));
    assertEquals(2, countOccurrences(source, "PRESERVE_DATABASE_ERROR_HANDLER"));
    assertTrue(source.contains("new DatabaseErrorHandler()"));
    assertTrue(source.contains("void onCorruption(SQLiteDatabase corruptedDatabase)"));
    assertFalse(source.contains("DefaultDatabaseErrorHandler"));
    assertFalse(source.contains("SQLiteDatabase.deleteDatabase"));
    assertFalse(source.contains(".delete("));
    assertFalse(source.contains(".renameTo("));
  }

  @Test
  void existingWalDatabaseIsOpenedReadWriteOnceWithoutCreateOrReadOnlyPreflight()
      throws Exception {
    String source = readSource("AndroidSQLiteCaptureDatabase.java");

    assertFalse(source.contains("SQLiteDatabase.OPEN_READONLY"));
    assertFalse(source.contains("isCompatibleReadOnly("));
    assertEquals(1, countOccurrences(source, "SQLiteDatabase.openDatabase("));
    assertTrue(source.contains(
        "int openFlags = SQLiteDatabase.OPEN_READWRITE"
            + "\n        | SQLiteDatabase.NO_LOCALIZED_COLLATORS;"));
    assertTrue(source.contains(
        "if (!existed) {\n"
            + "      openFlags |= SQLiteDatabase.CREATE_IF_NECESSARY;\n"
            + "    }"));
    assertOrdered(
        source,
        "int openFlags = SQLiteDatabase.OPEN_READWRITE",
        "SQLiteDatabase.openDatabase(",
        "if (existed)",
        "isCompatible(opened)");
  }

  @Test
  void rawQueryPragmasRemoveOnlyTheirStatementTerminatorAtOneBoundary() throws Exception {
    String source = readSource("AndroidSQLiteCaptureDatabase.java");

    assertTrue(source.contains("rawPragmaQuery(database, SET_JOURNAL_MODE_SQL)"));
    assertTrue(source.contains("queryPragmaLong(database, READ_USER_VERSION_SQL)"));
    assertTrue(source.contains("queryPragmaLong(database, READ_PAGE_SIZE_SQL)"));
    assertTrue(source.contains("queryPragmaLong(database, maximumPageCountSql(pageSize))"));
    assertTrue(source.contains(
        "database.rawQuery(withoutPragmaTerminator(pragmaSql), null)"));
    assertTrue(source.contains("database.rawQuery(SELECT_SCHEMA_SQL"));
    assertTrue(source.contains("database.rawQuery(sql, values)"));
  }

  @Test
  void sqliteAdapterNeverChangesFilesystemPermissions() throws Exception {
    String source = readSource("AndroidSQLiteCaptureDatabase.java");

    assertFalse(source.contains(".setReadable("));
    assertFalse(source.contains(".setWritable("));
    assertFalse(source.contains(".setExecutable("));
    assertFalse(source.contains(".listFiles("));
    assertFalse(source.contains("Files.walk("));
  }

  @Test
  void snapshotExporterUsesOnlyFixedFilesWithoutBroadWalkingOrGrants() throws Exception {
    String source = readSource("AndroidCaptureSharing.java");

    assertTrue(source.contains("DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME"));
    assertTrue(source.contains("DiagnosticConfig.OFFLINE_CAPTURE_MAX_DATABASE_BYTES"));
    assertTrue(source.contains(".getFD().sync()"));
    assertTrue(source.contains("StandardCopyOption.ATOMIC_MOVE"));
    assertTrue(source.contains("StandardCopyOption.REPLACE_EXISTING"));
    assertFalse(source.contains("AtomicMoveNotSupportedException"));
    assertFalse(source.contains(".listFiles("));
    assertFalse(source.contains("Files.list("));
    assertFalse(source.contains("Files.walk("));
    assertFalse(source.contains("FLAG_GRANT_PREFIX_URI_PERMISSION"));
    assertFalse(source.contains("FLAG_GRANT_WRITE_URI_PERMISSION"));
    assertFalse(source.contains("capture-v1.sqlite3"));
    assertFalse(source.contains("capture-v1-recovery.sqlite3"));
  }

  @Test
  void runtimePublishesInsideSuccessfulExtractionButNeverGeneralClose() throws Exception {
    String source = readSource("DiagnosticRuntime.java");
    String extraction = methodSlice(
        source, "public boolean quiesceForExtraction(long timeoutMillis)",
        "private boolean joinUntil(");
    String close = methodSlice(source, "public void close()", "@Override\n  public boolean");

    assertOrdered(
        extraction,
        "localSink.quiesceAndClose(",
        "networkSink.quiesceAndClose(",
        "extractionPublisher.publish(",
        "extractionState = 2");
    assertTrue(extraction.contains("extractionState = 3"));
    assertFalse(close.contains("extractionPublisher.publish("));

    String localPublisher = methodSlice(source, "boolean publishSnapshot(long deadlineNanos)",
        "private OfflineCaptureStore currentStore(");
    assertFalse(localPublisher.contains("AndroidSQLiteCaptureDatabase.open("));
    String currentStore = methodSlice(source, "private OfflineCaptureStore currentStore(",
        "\n  }\n}");
    assertOrdered(
        currentStore,
        "AndroidSQLiteCaptureDatabase.open(directory)",
        "canonicalDatabase = database.snapshotSource()");
  }

  @Test
  void successfulOpenAndTransactionNeverRefreshFilesystemPermissions() throws Exception {
    String source = readSource("AndroidSQLiteCaptureDatabase.java");

    String openCandidate = methodSlice(
        source, "private static AndroidSQLiteCaptureDatabase openCandidate(",
        "private static boolean isCompatible(");
    assertOrdered(openCandidate, "configure(opened);",
        "new AndroidSQLiteCaptureDatabase(opened, file)");

    String beginTransaction = methodSlice(source, "public void beginTransaction()",
        "public void ensureRun(");
    assertTrue(beginTransaction.contains("database.beginTransaction();"));

    String setSuccessful = methodSlice(source, "public void setTransactionSuccessful()",
        "public void endTransaction()");
    assertTrue(setSuccessful.contains("database.setTransactionSuccessful();"));

    String endTransaction = methodSlice(source, "public void endTransaction()",
        "public void close()");
    assertFalse(endTransaction.contains("setReadable"));

    String close = methodSlice(source, "public void close()",
        "private static AndroidSQLiteCaptureDatabase openCandidate(");
    assertFalse(close.contains("setReadable"));
    assertTrue(close.contains("checkpoint.getLong(0) != 0L"));
    assertTrue(close.contains("checkpoint.getLong(1) != 0L"));
    assertTrue(close.contains("checkpoint.getLong(2) != 0L"));
  }

  private static void assertSourceImports(String sourceName, String importedType) throws Exception {
    assertTrue(readSource(sourceName).contains("import " + importedType + ";"));
  }

  private static void assertNoOtherPayloadSourceImports(String importPrefix) throws Exception {
    try (Stream<Path> sources = Files.list(PAYLOAD_SOURCE_DIRECTORY)) {
      for (Path source : sources.filter(path -> path.toString().endsWith(".java")).toList()) {
        String name = source.getFileName().toString();
        if (name.equals("AndroidApplicationFiles.java")
            || name.equals("AndroidCaptureSharing.java")
            || name.equals("AndroidSQLiteCaptureDatabase.java")) {
          continue;
        }
        assertFalse(
            Files.readString(source).contains("import " + importPrefix),
            () -> name + " must not import " + importPrefix);
      }
    }
  }

  private static String readSource(String sourceName) throws IOException {
    Path source = PAYLOAD_SOURCE_DIRECTORY.resolve(sourceName);
    assertTrue(Files.isRegularFile(source), () -> "missing payload source " + sourceName);
    return Files.readString(source);
  }

  private static String normalizeWhitespace(String value) {
    return value.replaceAll("\\s+", " ").trim();
  }

  private static int countOccurrences(String value, String token) {
    int count = 0;
    int offset = 0;
    while ((offset = value.indexOf(token, offset)) >= 0) {
      count++;
      offset += token.length();
    }
    return count;
  }

  private static List<String> stringArrayConstant(String source, String name) {
    Pattern declaration = Pattern.compile(
        "(?:private\\s+)?static\\s+final\\s+String\\[\\]\\s+" + Pattern.quote(name)
            + "\\s*=\\s*new\\s+String\\[\\]\\s*\\{(.*?)\\};",
        Pattern.DOTALL);
    Matcher declarationMatch = declaration.matcher(source);
    assertTrue(declarationMatch.find(), () -> "missing string array constant " + name);
    Matcher literals = Pattern.compile("\"((?:\\\\.|[^\"\\\\])*)\"")
        .matcher(declarationMatch.group(1));
    java.util.ArrayList<String> values = new java.util.ArrayList<>();
    while (literals.find()) {
      values.add(literals.group(1));
    }
    return values;
  }

  private static String methodSlice(String source, String startToken, String endToken) {
    int start = source.indexOf(startToken);
    assertTrue(start >= 0, () -> "missing method start " + startToken);
    int end = source.indexOf(endToken, start + startToken.length());
    assertTrue(end > start, () -> "missing method end " + endToken);
    return source.substring(start, end);
  }

  private static void assertOrdered(String value, String... tokens) {
    int previous = -1;
    for (String token : tokens) {
      int current = value.indexOf(token, previous + 1);
      assertTrue(current > previous, () -> "missing or out-of-order token " + token);
      previous = current;
    }
  }

  private static String stringConstant(String source, String name) {
    Pattern declaration = Pattern.compile(
        "(?:private\\s+)?static\\s+final\\s+String\\s+" + Pattern.quote(name)
            + "\\s*=\\s*(.*?);\\s*(?:\\r?\\n)",
        Pattern.DOTALL);
    Matcher declarationMatch = declaration.matcher(source);
    assertTrue(declarationMatch.find(), () -> "missing string constant " + name);
    Matcher literals = Pattern.compile("\"((?:\\\\.|[^\"\\\\])*)\"")
        .matcher(declarationMatch.group(1));
    StringBuilder value = new StringBuilder();
    while (literals.find()) {
      value.append(literals.group(1)
          .replace("\\n", "\n")
          .replace("\\\"", "\"")
          .replace("\\\\", "\\"));
    }
    return value.toString();
  }
}
