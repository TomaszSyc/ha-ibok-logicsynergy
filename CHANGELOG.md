# Changelog

## [1.0.3](https://github.com/TomaszSyc/ha-ibok-logicsynergy/compare/v1.0.2...v1.0.3) (2026-10-21)


### Features

* reconfigure the account and remove a meter that left the portal ([7bd824a](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/7bd824ad77639da14405e8fa6555995b602f20d4))

## [1.0.2](https://github.com/TomaszSyc/ha-ibok-logicsynergy/compare/v1.0.1...v1.0.2) (2026-10-19)


### Bug Fixes

* count a meter replaced under the same number from its first reading ([a3c9f01](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/a3c9f01ea862f22c40ccc49150e0db3c4236c9c7))

## [1.0.1](https://github.com/TomaszSyc/ha-ibok-logicsynergy/compare/v1.0.0...v1.0.1) (2026-10-01)

## [1.0.0](https://github.com/TomaszSyc/ha-ibok-logicsynergy/compare/v1.0.0-beta.1...v1.0.0) (2026-10-01)


### Features

* submit like the portal's form and confirm the reading from its record ([a804e39](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/a804e3969cf4dff9ac77f6cc99e76dcde8cf35ea))

## [1.0.0-beta.1](https://github.com/TomaszSyc/ha-ibok-logicsynergy/compare/v0.2.0...v1.0.0-beta.1) (2026-10-01)


### Bug Fixes

* renew expired portal sessions and harden submitting a reading ([56336ab](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/56336ab90ee61224893b9e3d4d12264bffa2c181))

## [0.2.0](https://github.com/TomaszSyc/ha-ibok-logicsynergy/compare/v0.1.19...v0.2.0) (2026-09-25)


### Features

* price per m3, payment due, legalisation, diagnostics and a typed reading ([3822636](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/3822636e3edc73b45393699563f4f8cc702dc6f3))

## [0.1.19](https://github.com/TomaszSyc/ha-ibok-logicsynergy/compare/v0.1.17...v0.1.19) (2026-09-24)


### Bug Fixes

* keep working when one module fails and pick up new meters ([8b2b66c](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/8b2b66c051d938d56617897b6965a53645ae6523))

## [0.1.17](https://github.com/TomaszSyc/ha-ibok-logicsynergy/compare/v0.1.13...v0.1.17) (2026-09-24)


### Bug Fixes

* keep meter assignments, clean statistics and a deliberate second press ([49cdd3b](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/49cdd3b02894c88d4b68f16cfa7091e5438cff8f))
* submit on a live session and keep the password on the portal ([9912f73](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/9912f73518c39842d0b9584b918b7322e952f72d))

## 0.1.13 (2026-09-23)


### Features

* confirm a submission with a second press of the button ([89547c5](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/89547c51e3628810a8cfa9e8379ef773bfb3e8b1))
* read an iBOK account and submit a meter reading from Home Assistant ([3345d5a](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/3345d5a83c80f1c87c1a7de5c139f95bcfbf255b))
* ship a brand icon so the integration is not shown as a placeholder ([78652b6](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/78652b6c0cb3d756d781abb948e15997739f285f))
* submit the reading at the precision the meter is actually read at ([4555915](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/4555915a30630c4bda74727365c0c6a326807f01))


### Bug Fixes

* drop the angle brackets that broke the options form description ([0d91640](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/0d916408c366d841a55672cbcba48667703c17fb))
* name meter entities without a placeholder Home Assistant never fills ([638ad6a](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/638ad6aeba3968b81cb544a0bb8e5e8a5b93768e))
* pass Home Assistant manifest validation and the HACS schema ([64941e9](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/64941e9b8720b7c3719f98ae835e1cc62373f647))
* pin pytest to the version the Home Assistant test plugin demands ([f2cea66](https://github.com/TomaszSyc/ha-ibok-logicsynergy/commit/f2cea66f07200ad412545c6282cb5b9dd192e5b6))
