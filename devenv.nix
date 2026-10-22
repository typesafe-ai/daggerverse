{
  pkgs,
  inputs,
  ...
}: let
  pkgs-unstable = import inputs.nixpkgs-unstable {inherit (pkgs) system;};
  # dagger/nix only packages stable releases; track the 1.0 betas until 1.0.0 lands there.
  dagger = inputs.dagger.packages.${pkgs.system}.dagger.overrideAttrs (_: let
    version = "1.0.0-beta.20";
    platform =
      {
        x86_64-linux = "linux_amd64";
        aarch64-linux = "linux_arm64";
        x86_64-darwin = "darwin_amd64";
        aarch64-darwin = "darwin_arm64";
      }.${
        pkgs.system
      };
  in {
    inherit version;
    src = pkgs.fetchurl {
      url = "https://dl.dagger.io/dagger/releases/${version}/dagger_v${version}_${platform}.tar.gz";
      sha256 =
        {
          linux_amd64 = "5982b67992b55cc1cc8636509a0f672a401368a16b865bbe1d257195e7b516ae";
          linux_arm64 = "7b9a9989092832006f6188d92b60f92375a784bf2c92217b083999be6c6e2874";
          darwin_amd64 = "4707d7573a95d2db314b19abe8dc28620b30eadd7a369b57a14312942690071d";
          darwin_arm64 = "19e623d264b217570c0f8b84d85c8321c2e63f164fcdd4426f3e49245ec642cf";
        }.${
          platform
        };
    };
  });
in {
  # https://devenv.sh/packages/
  packages = [
    dagger
    pkgs.pinact
    pkgs.zizmor
    pkgs.prek
    pkgs.git-cliff
    pkgs-unstable.uv
  ];
}
