package ai.comma.naver.payload;

import android.database.Cursor;
import android.database.DatabaseErrorHandler;
import android.database.sqlite.SQLiteDatabase;
import java.io.File;
import java.io.IOException;
import java.util.HashMap;
import java.util.Map;

final class AndroidSQLiteCaptureDatabase implements CaptureDatabase {
  private static final String CREATE_META_TABLE_SQL =
      "CREATE TABLE capture_meta (\n"
          + "  key TEXT PRIMARY KEY NOT NULL,\n"
          + "  value TEXT NOT NULL\n"
          + ");";
  private static final String CREATE_RUNS_TABLE_SQL =
      "CREATE TABLE capture_runs (\n"
          + "  run_id TEXT PRIMARY KEY NOT NULL,\n"
          + "  started_monotonic_ns INTEGER NOT NULL,\n"
          + "  payload_build_id TEXT NOT NULL,\n"
          + "  clean_shutdown INTEGER NOT NULL DEFAULT 0\n"
          + "    CHECK(clean_shutdown IN (0, 1))\n"
          + ");";
  private static final String CREATE_FRAMES_TABLE_SQL =
      "CREATE TABLE capture_frames (\n"
          + "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
          + "  run_id TEXT NOT NULL,\n"
          + "  sequence INTEGER NOT NULL CHECK(sequence >= 1),\n"
          + "  monotonic_ns INTEGER NOT NULL,\n"
          + "  channel TEXT NOT NULL CHECK(channel IN (\n"
          + "    'status', 'tbt_current', 'tbt_next', 'safety', 'route', 'lane'\n"
          + "  )),\n"
          + "  object_dropped_before INTEGER NOT NULL CHECK(object_dropped_before >= 0),\n"
          + "  encoded_json TEXT NOT NULL,\n"
          + "  pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0, 1))\n"
          + ");";
  private static final String CREATE_FRAMES_INDEX_SQL =
      "CREATE INDEX capture_frames_channel_id\n"
          + "ON capture_frames(channel, id);";
  private static final String READ_USER_VERSION_SQL = "PRAGMA user_version;";
  private static final String SET_USER_VERSION_SQL =
      "PRAGMA user_version=" + DiagnosticConfig.OFFLINE_CAPTURE_SCHEMA_VERSION + ";";
  private static final String SET_JOURNAL_MODE_SQL = "PRAGMA journal_mode=WAL;";
  private static final String SET_SYNCHRONOUS_SQL = "PRAGMA synchronous=NORMAL;";
  private static final String SET_WAL_AUTOCHECKPOINT_SQL =
      "PRAGMA wal_autocheckpoint="
          + DiagnosticConfig.OFFLINE_CAPTURE_WAL_AUTOCHECKPOINT_PAGES + ";";
  private static final String CHECKPOINT_FOR_EXTRACTION_SQL =
      "PRAGMA wal_checkpoint(TRUNCATE);";
  private static final String SET_JOURNAL_SIZE_LIMIT_SQL =
      "PRAGMA journal_size_limit="
          + DiagnosticConfig.OFFLINE_CAPTURE_JOURNAL_SIZE_LIMIT_BYTES + ";";
  private static final String READ_PAGE_SIZE_SQL = "PRAGMA page_size;";

  private static final String SELECT_SCHEMA_SQL =
      "SELECT type, name, sql FROM sqlite_master "
          + "WHERE name NOT LIKE ? ORDER BY type, name";
  private static final String INSERT_RUN_SQL =
      "INSERT OR IGNORE INTO capture_runs "
          + "(run_id, started_monotonic_ns, payload_build_id, clean_shutdown) "
          + "VALUES (?, ?, ?, ?)";
  private static final String MARK_RUN_CLEAN_SQL =
      "UPDATE capture_runs SET clean_shutdown=? WHERE run_id=?";
  private static final String COUNT_ROWS_SQL =
      "SELECT COUNT(*) FROM capture_frames WHERE channel=?";
  private static final String COUNT_PINNED_ROWS_SQL =
      "SELECT COUNT(*) FROM capture_frames WHERE channel=? AND pinned=?";
  private static final String INSERT_FRAME_SQL =
      "INSERT INTO capture_frames "
          + "(run_id, sequence, monotonic_ns, channel, object_dropped_before, encoded_json, pinned) "
          + "VALUES (?, ?, ?, ?, ?, ?, ?)";
  private static final String DELETE_OLDEST_UNPINNED_SQL =
      "DELETE FROM capture_frames WHERE id IN ("
          + "SELECT id FROM capture_frames WHERE channel=? AND pinned=? "
          + "ORDER BY id LIMIT ?)";
  private static final String PUT_META_SQL =
      "INSERT OR REPLACE INTO capture_meta (key, value) VALUES (?, ?)";

  private static final String[] METADATA_KEYS = new String[] {
      "schema_version",
      "payload_build_id",
      "object_queue_dropped",
      "local_queue_dropped",
      "network_queue_dropped",
      "storage_errors",
      "network_errors",
      "retention_deletes",
      "rejected_frames",
  };
  private static final DatabaseErrorHandler PRESERVE_DATABASE_ERROR_HANDLER =
      new DatabaseErrorHandler() {
        @Override
        public void onCorruption(SQLiteDatabase corruptedDatabase) {
          // Preserve the database and sidecars; the failed open/query is handled as incompatible.
        }
      };

  private final SQLiteDatabase database;
  private final File databaseFile;

  private AndroidSQLiteCaptureDatabase(SQLiteDatabase database, File databaseFile) {
    this.database = database;
    this.databaseFile = databaseFile;
  }

  static AndroidSQLiteCaptureDatabase open(File directory) throws IOException {
    if (directory == null
        || !DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME.equals(directory.getName())
        || !directory.isDirectory()) {
      throw new IOException("capture internal directory is unavailable");
    }

    AndroidSQLiteCaptureDatabase primary = openCandidate(
        new File(directory, DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_NAME));
    if (primary != null) {
      return primary;
    }
    AndroidSQLiteCaptureDatabase recovery = openCandidate(
        new File(directory, DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_RECOVERY_NAME));
    if (recovery != null) {
      return recovery;
    }
    throw new IOException("capture databases are incompatible");
  }

  File snapshotSource() {
    return databaseFile;
  }

  @Override
  public void beginTransaction() throws IOException {
    try {
      database.beginTransaction();
    } catch (RuntimeException exception) {
      throw failure("failed to begin capture transaction", exception);
    }
  }

  @Override
  public void ensureRun(
      String runId, long startedMonotonicNs, String buildId) throws IOException {
    execute(
        INSERT_RUN_SQL,
        new Object[] {runId, startedMonotonicNs, buildId, 0},
        "failed to persist capture run");
  }

  @Override
  public void markRunClean(String runId) throws IOException {
    execute(MARK_RUN_CLEAN_SQL, new Object[] {1, runId}, "failed to mark capture run clean");
  }

  @Override
  public long countRows(String channel) throws IOException {
    return queryLong(COUNT_ROWS_SQL, new String[] {channel});
  }

  @Override
  public long countPinnedRows(String channel) throws IOException {
    return queryLong(COUNT_PINNED_ROWS_SQL, new String[] {channel, "1"});
  }

  @Override
  public void insert(String runId, EncodedDiagnosticFrame frame, boolean pinned)
      throws IOException {
    execute(
        INSERT_FRAME_SQL,
        new Object[] {
            runId,
            frame.sequence(),
            frame.monotonicNs(),
            frame.channel(),
            frame.objectDroppedBefore(),
            frame.encodedJson(),
            pinned ? 1 : 0,
        },
        "failed to persist capture frame");
  }

  @Override
  public void deleteOldestUnpinned(String channel, long count) throws IOException {
    execute(
        DELETE_OLDEST_UNPINNED_SQL,
        new Object[] {channel, 0, count},
        "failed to prune capture frames");
  }

  @Override
  public void putMeta(String key, String value) throws IOException {
    if (!isMetadataKey(key) || value == null) {
      throw new IOException("unsupported capture metadata");
    }
    execute(PUT_META_SQL, new Object[] {key, value}, "failed to persist capture metadata");
  }

  @Override
  public void setTransactionSuccessful() throws IOException {
    try {
      database.setTransactionSuccessful();
    } catch (RuntimeException exception) {
      throw failure("failed to commit capture transaction", exception);
    }
  }

  @Override
  public void endTransaction() throws IOException {
    try {
      database.endTransaction();
    } catch (RuntimeException exception) {
      throw failure("failed to end capture transaction", exception);
    }
  }

  @Override
  public void close() {
    RuntimeException failure = null;
    try {
      Cursor checkpoint = rawPragmaQuery(database, CHECKPOINT_FOR_EXTRACTION_SQL);
      try {
        if (!checkpoint.moveToFirst()
            || checkpoint.getLong(0) != 0L
            || checkpoint.getLong(1) != 0L
            || checkpoint.getLong(2) != 0L) {
          failure = new IllegalStateException("capture checkpoint did not complete");
        }
      } finally {
        checkpoint.close();
      }
    } catch (RuntimeException exception) {
      failure = exception;
    }
    try {
      database.close();
    } catch (RuntimeException exception) {
      if (failure == null) {
        failure = exception;
      }
    }
    if (failure != null) {
      throw new IllegalStateException("failed to close capture database");
    }
  }

  private static AndroidSQLiteCaptureDatabase openCandidate(File file) throws IOException {
    boolean existed = file.isFile();
    int openFlags = SQLiteDatabase.OPEN_READWRITE
        | SQLiteDatabase.NO_LOCALIZED_COLLATORS;
    if (!existed) {
      openFlags |= SQLiteDatabase.CREATE_IF_NECESSARY;
    }

    SQLiteDatabase opened = null;
    try {
      opened = SQLiteDatabase.openDatabase(
          file.getAbsolutePath(),
          null,
          openFlags,
          PRESERVE_DATABASE_ERROR_HANDLER);
      if (existed) {
        if (!isCompatible(opened)) {
          closeQuietly(opened);
          return null;
        }
      } else {
        initialize(opened);
      }
      configure(opened);
    } catch (IOException | RuntimeException exception) {
      closeQuietly(opened);
      return null;
    }
    return new AndroidSQLiteCaptureDatabase(opened, file);
  }

  private static boolean isCompatible(SQLiteDatabase database) {
    try {
      if (queryPragmaLong(database, READ_USER_VERSION_SQL)
          != DiagnosticConfig.OFFLINE_CAPTURE_SCHEMA_VERSION) {
        return false;
      }
      Map<String, String> expected = new HashMap<>();
      expected.put("table:capture_meta", CREATE_META_TABLE_SQL);
      expected.put("table:capture_runs", CREATE_RUNS_TABLE_SQL);
      expected.put("table:capture_frames", CREATE_FRAMES_TABLE_SQL);
      expected.put("index:capture_frames_channel_id", CREATE_FRAMES_INDEX_SQL);

      Cursor cursor = database.rawQuery(SELECT_SCHEMA_SQL, new String[] {"sqlite_%"});
      try {
        while (cursor.moveToNext()) {
          String key = cursor.getString(0) + ":" + cursor.getString(1);
          String sql = expected.remove(key);
          if (sql == null || !normalizeSql(sql).equals(normalizeSql(cursor.getString(2)))) {
            return false;
          }
        }
      } finally {
        cursor.close();
      }
      return expected.isEmpty();
    } catch (RuntimeException exception) {
      return false;
    }
  }

  private static void initialize(SQLiteDatabase database) throws IOException {
    boolean began = false;
    try {
      database.beginTransaction();
      began = true;
      database.execSQL(CREATE_META_TABLE_SQL);
      database.execSQL(CREATE_RUNS_TABLE_SQL);
      database.execSQL(CREATE_FRAMES_TABLE_SQL);
      database.execSQL(CREATE_FRAMES_INDEX_SQL);
      putInitialMetadata(database);
      database.execSQL(SET_USER_VERSION_SQL);
      database.setTransactionSuccessful();
    } catch (RuntimeException exception) {
      throw failure("failed to initialize capture database", exception);
    } finally {
      if (began) {
        try {
          database.endTransaction();
        } catch (RuntimeException exception) {
          throw failure("failed to end capture initialization", exception);
        }
      }
    }
  }

  private static void putInitialMetadata(SQLiteDatabase database) {
    database.execSQL(
        PUT_META_SQL,
        new Object[] {
            "schema_version",
            Integer.toString(DiagnosticConfig.OFFLINE_CAPTURE_SCHEMA_VERSION),
        });
    database.execSQL(
        PUT_META_SQL,
        new Object[] {"payload_build_id", DiagnosticConfig.OFFLINE_CAPTURE_PAYLOAD_BUILD_ID});
    for (int index = 2; index < METADATA_KEYS.length; index++) {
      database.execSQL(PUT_META_SQL, new Object[] {METADATA_KEYS[index], "0"});
    }
  }

  private static void configure(SQLiteDatabase database) throws IOException {
    try {
      Cursor journalMode = rawPragmaQuery(database, SET_JOURNAL_MODE_SQL);
      try {
        if (!journalMode.moveToFirst() || !"wal".equalsIgnoreCase(journalMode.getString(0))) {
          throw new IOException("failed to enable capture WAL");
        }
      } finally {
        journalMode.close();
      }
      database.execSQL(SET_SYNCHRONOUS_SQL);
      Cursor autoCheckpoint = rawPragmaQuery(database, SET_WAL_AUTOCHECKPOINT_SQL);
      try {
        if (!autoCheckpoint.moveToFirst()
            || autoCheckpoint.getLong(0)
                != DiagnosticConfig.OFFLINE_CAPTURE_WAL_AUTOCHECKPOINT_PAGES) {
          throw new IOException("failed to configure capture WAL checkpoint");
        }
      } finally {
        autoCheckpoint.close();
      }
      long journalSizeLimit = queryPragmaLong(database, SET_JOURNAL_SIZE_LIMIT_SQL);
      if (journalSizeLimit != DiagnosticConfig.OFFLINE_CAPTURE_JOURNAL_SIZE_LIMIT_BYTES) {
        throw new IOException("failed to configure capture journal size limit");
      }
      long pageSize = queryPragmaLong(database, READ_PAGE_SIZE_SQL);
      if (pageSize <= 0L) {
        throw new IOException("invalid capture page size");
      }
      long maximumPages = DiagnosticConfig.OFFLINE_CAPTURE_MAX_DATABASE_BYTES / pageSize;
      long appliedMaximumPages =
          queryPragmaLong(database, maximumPageCountSql(pageSize));
      if (appliedMaximumPages != maximumPages) {
        throw new IOException("failed to enforce capture database ceiling");
      }
    } catch (RuntimeException exception) {
      throw failure("failed to configure capture database", exception);
    }
  }

  private void execute(String sql, Object[] values, String message) throws IOException {
    try {
      database.execSQL(sql, values);
    } catch (RuntimeException exception) {
      throw failure(message, exception);
    }
  }

  private long queryLong(String sql, String[] values) throws IOException {
    try {
      return queryLong(database, sql, values);
    } catch (RuntimeException exception) {
      throw failure("failed to query capture database", exception);
    }
  }

  private static long queryLong(SQLiteDatabase database, String sql, String[] values) {
    Cursor cursor = database.rawQuery(sql, values);
    try {
      if (!cursor.moveToFirst()) {
        throw new IllegalStateException("capture query returned no row");
      }
      return cursor.getLong(0);
    } finally {
      cursor.close();
    }
  }

  private static long queryPragmaLong(SQLiteDatabase database, String pragmaSql) {
    Cursor cursor = rawPragmaQuery(database, pragmaSql);
    try {
      if (!cursor.moveToFirst()) {
        throw new IllegalStateException("capture PRAGMA returned no row");
      }
      return cursor.getLong(0);
    } finally {
      cursor.close();
    }
  }

  private static Cursor rawPragmaQuery(SQLiteDatabase database, String pragmaSql) {
    return database.rawQuery(withoutPragmaTerminator(pragmaSql), null);
  }

  private static String withoutPragmaTerminator(String pragmaSql) {
    if (pragmaSql == null
        || !pragmaSql.startsWith("PRAGMA ")
        || !pragmaSql.endsWith(";")
        || pragmaSql.indexOf(';') != pragmaSql.length() - 1) {
      throw new IllegalArgumentException("invalid capture PRAGMA");
    }
    return pragmaSql.substring(0, pragmaSql.length() - 1);
  }

  private static boolean isMetadataKey(String key) {
    for (String allowed : METADATA_KEYS) {
      if (allowed.equals(key)) {
        return true;
      }
    }
    return false;
  }

  private static String maximumPageCountSql(long pageSize) throws IOException {
    if (pageSize == 512L) return "PRAGMA max_page_count=131072;";
    if (pageSize == 1024L) return "PRAGMA max_page_count=65536;";
    if (pageSize == 2048L) return "PRAGMA max_page_count=32768;";
    if (pageSize == 4096L) return "PRAGMA max_page_count=16384;";
    if (pageSize == 8192L) return "PRAGMA max_page_count=8192;";
    if (pageSize == 16384L) return "PRAGMA max_page_count=4096;";
    if (pageSize == 32768L) return "PRAGMA max_page_count=2048;";
    if (pageSize == 65536L) return "PRAGMA max_page_count=1024;";
    throw new IOException("unsupported capture page size");
  }

  private static String normalizeSql(String sql) {
    if (sql == null) {
      return "";
    }
    String normalized = sql.trim().replaceAll("\\s+", " ");
    return normalized.endsWith(";")
        ? normalized.substring(0, normalized.length() - 1)
        : normalized;
  }

  private static IOException failure(String message, RuntimeException exception) {
    return new IOException(message, exception);
  }

  private static void closeQuietly(SQLiteDatabase opened) {
    try {
      if (opened != null && opened.isOpen()) {
        opened.close();
      }
    } catch (RuntimeException ignored) {
      // A failed compatibility probe must not prevent trying the recovery database.
    }
  }
}
