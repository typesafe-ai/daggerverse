{
  pkgs,
  inputs,
  ...
}: let
  pkgs-unstable = import inputs.nixpkgs-unstable {inherit (pkgs) system;};
  # dagger/nix only packages stable releases; track the 1.0 betas until 1.0.0 lands there.
  dagger = inputs.dagger.packages.${pkgs.system}.dagger.overrideAttrs (_: let
    version = "1.0.0-beta.24";
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
          linux_amd64 = "648a5ffcf7d6d724526d20942066fd023cdf2b8284204d0ca3e1fcf6f0793f6e";
          linux_arm64 = "c6fd1d9fa53ac4880a21373411a923720cd65e6b7a05a617379278a524ee078d";
          darwin_amd64 = "16b879a083b8ba0be8db80d58701e2726645adbcdd90880417991813c5133102";
          darwin_arm64 = "f0b5bfe032f8d245633ee03c350fcc4ad7089bf46a1f86db0a8dddf3011ca5fc";
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
