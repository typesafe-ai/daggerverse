{
  pkgs,
  inputs,
  ...
}: {
  # https://devenv.sh/packages/
  packages = [
    inputs.dagger.packages.${pkgs.system}.dagger
  ];
}
