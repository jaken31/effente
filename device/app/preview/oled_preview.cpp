// Laptop preview of the OLED: the sketch's own renderer (oled_ui.h), fed a layout on stdin.
// Writes the 1024-byte frame (8 pages x 128 columns, bit 0 the top pixel) to stdout.
//
//   oled_preview MG PEAK_G TEMP_C MS [SPARK_MG...]  < layout.txt  > frame.bin
//
// The trace is filled from SPARK_MG values (oldest first), or from a gentle wobble around MG.
// Built and run by `python -m device.app.preview`.
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

#include "../effente/sketch/oled_ui.h"

int main(int argc, char **argv) {
  if (argc < 5) {
    fprintf(stderr, "usage: oled_preview MG PEAK_G TEMP_C MS [SPARK_MG...] < layout\n");
    return 2;
  }
  ui::Live live = {(float)atof(argv[1]), (float)atof(argv[2]), (float)atof(argv[3]), (uint32_t)atol(argv[4])};
  static char buf[1024];
  size_t n = fread(buf, 1, sizeof buf - 1, stdin);
  buf[n] = 0;
  if (n && buf[n - 1] == '\n') buf[n - 1] = 0;
  ui::setLayout(buf);
  if (argc > 5) {
    for (int i = 0; i < ui::W; i++) ui::pushSpark((float)atof(argv[5 + i * (argc - 5) / ui::W]));
  } else {
    for (int i = 0; i < ui::W; i++)
      ui::pushSpark(live.mg * (1.0f + 0.25f * sinf(i * 0.35f) + 0.12f * sinf(i * 1.7f + 1.0f)));
  }
  ui::compose(live);
  fwrite(ui::fb, 1, sizeof ui::fb, stdout);
  return 0;
}
