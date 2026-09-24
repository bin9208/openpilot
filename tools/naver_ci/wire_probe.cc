#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include "selfdrive/carrot/realtime/compact_state_native.h"

int main(int argc, char **argv) {
  if (argc != 2) return 2;
  std::ifstream input(argv[1], std::ios::binary);
  if (!input) return 3;
  const std::string data((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
  const auto result = encode_carrot_state_compact_frame("carrotMan", 9, data.data(), data.size(), 7);
  std::cout.write(result.data(), result.size());
}
