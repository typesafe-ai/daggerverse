{
  pkgs,
  inputs,
  ...
}: let
  pkgs-unstable = import inputs.nixpkgs-unstable {inherit (pkgs) system;};
  # dagger/nix only packages stable releases; track the 1.0 betas until 1.0.0 lands there.
  dagger = inputs.dagger.packages.${pkgs.system}.dagger.overrideAttrs (_: let
    version = "1.0.0-beta.25";
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
          linux_amd64 = "32405fd37daa95d9444e9faf66b9d6bd1ddd1580916f145fa2fc6f3cec6e7b69";
          linux_arm64 = "0f5abed7003cfaa2b6bcbb85286c802d0119336a805f54589f3859c9a4f02b0a";
          darwin_amd64 = "0705f500896e2de340c9327f4337b0e7f022365a8b68d362f3c9e56a96f30359";
          darwin_arm64 = "b15fc7bb2d2c1545854d118d9410093768a745ca6320adea5f97d6d1e18fda51";
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
    # gofmt for the prek hook on the Go modules.
    pkgs.go
  ];
}
