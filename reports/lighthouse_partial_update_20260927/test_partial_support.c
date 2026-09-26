#include <assert.h>
#include <math.h>
#include "optical_partial_support.h"

int main(void) {
	assert(!survive_optical_partial_supported(2, 1, 2, 3, 1, 0.));
	assert(!survive_optical_partial_supported(2, 1, 2, 3, 0, 16.));
	assert(!survive_optical_partial_supported(2, 1, 1, 2, 1, 16.));
	assert(!survive_optical_partial_supported(2, 1, 2, 2, 1, 16.));
	assert(!survive_optical_partial_supported(0, 1, 2, 3, 1, 16.));
	assert(!survive_optical_partial_supported(2, 2, 2, 4, 1, 16.));
	assert(!survive_optical_partial_supported(2, 1, 2, 3, 1, NAN));
	assert(!survive_optical_partial_supported(2, 1, 2, 3, 1, INFINITY));
	assert(survive_optical_partial_supported(2, 1, 2, 3, 1, 16.));
	return 0;
}
