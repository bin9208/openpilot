package ai.comma.naver.payload;

import java.io.Closeable;
import java.io.IOException;

interface NavigationTransport extends Closeable {
  void send(String line) throws IOException;

  @Override
  void close();
}
