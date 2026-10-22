{
  pkgs,
  inputs,
  ...
}: let
  pkgs-unstable = import inputs.nixpkgs-unstable {inherit (pkgs) system;};
  # dagger/nix only packages stable releases; track the 1.0 betas until 1.0.0 lands there.
  dagger = inputs.dagger.packages.${pkgs.system}.dagger.overrideAttrs (_: let
    version = "1.0.0-beta.19";
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
          linux_amd64 = "fecc67355fb3baff9da548d8f05c468a9c404aeb8d9498801f199fbea78038a0";
          linux_arm64 = "89be820ea938ad13373d943ac6bac036478d51cc2eb36dfdea36e625859907d8";
          darwin_amd64 = "d3c7c36c7003461b10785297f6e10b07a8cfe2a92ef0db052b2e750ad7167ff8";
          darwin_arm64 = "57d3fe6fa9107759427ea651921a279462ffec194b48140fd50ce555d13b0605";
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
