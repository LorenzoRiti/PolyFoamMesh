#pragma once

#include "Types.h"
#include "Mesher.h"
#include <memory>

namespace autopoly {

std::unique_ptr<Mesher> createMesher();

} // namespace autopoly