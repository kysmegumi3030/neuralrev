# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file LICENSE.rst or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION ${CMAKE_VERSION}) # this file comes with cmake

# If CMAKE_DISABLE_SOURCE_CHANGES is set to true and the source directory is an
# existing directory in our source tree, calling file(MAKE_DIRECTORY) on it
# would cause a fatal error, even though it would be a no-op.
if(NOT EXISTS "/Users/realmac/Documents/coding/JucyPWA1/build/_deps/juce-src")
  file(MAKE_DIRECTORY "/Users/realmac/Documents/coding/JucyPWA1/build/_deps/juce-src")
endif()
file(MAKE_DIRECTORY
  "/Users/realmac/Documents/trae_projects/neuralrev/build/_deps/juce-build"
  "/Users/realmac/Documents/trae_projects/neuralrev/build/_deps/juce-subbuild/juce-populate-prefix"
  "/Users/realmac/Documents/trae_projects/neuralrev/build/_deps/juce-subbuild/juce-populate-prefix/tmp"
  "/Users/realmac/Documents/trae_projects/neuralrev/build/_deps/juce-subbuild/juce-populate-prefix/src/juce-populate-stamp"
  "/Users/realmac/Documents/trae_projects/neuralrev/build/_deps/juce-subbuild/juce-populate-prefix/src"
  "/Users/realmac/Documents/trae_projects/neuralrev/build/_deps/juce-subbuild/juce-populate-prefix/src/juce-populate-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "/Users/realmac/Documents/trae_projects/neuralrev/build/_deps/juce-subbuild/juce-populate-prefix/src/juce-populate-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "/Users/realmac/Documents/trae_projects/neuralrev/build/_deps/juce-subbuild/juce-populate-prefix/src/juce-populate-stamp${cfgdir}") # cfgdir has leading slash
endif()
