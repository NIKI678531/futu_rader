const path = require('node:path')
const webpack = require('webpack')
const HtmlWebpackPlugin = require('html-webpack-plugin')

const { ModuleFederationPlugin } = webpack.container
const dependencies = require('./package.json').dependencies

module.exports = (_env, argv = {}) => {
  const production = argv.mode === 'production'
  const configuredBasePath = process.env.VITE_BASE_PATH || (production ? '/remote/opinion-radar/' : '/')
  const appBasePath = `/${configuredBasePath.replace(/^\/+|\/+$/g, '')}/`.replace(/^\/\/$/, '/')
  const apiBasePath = process.env.VITE_API_BASE || (production ? '/remote/opinion-radar/api/v1' : '/api/v1')
  const publicPath = production ? appBasePath : 'auto'

  return {
    mode: production ? 'production' : 'development',
    entry: './src/index.jsx',
    output: {
      path: path.resolve(__dirname, 'dist'),
      filename: 'assets/[name].[contenthash:8].js',
      chunkFilename: 'assets/[name].[contenthash:8].js',
      assetModuleFilename: 'assets/[name].[contenthash:8][ext]',
      publicPath,
      uniqueName: 'opinionRadar',
      clean: true,
    },
    module: {
      rules: [
        {
          test: /\.m?js$/,
          resolve: { fullySpecified: false },
        },
        {
          test: /\.[jt]sx?$/,
          exclude: /node_modules/,
          use: {
            loader: 'babel-loader',
            options: {
              presets: [
                ['@babel/preset-env', { targets: 'defaults' }],
                ['@babel/preset-react', { runtime: 'automatic' }],
              ],
            },
          },
        },
        {
          test: /\.css$/i,
          use: [
            'style-loader',
            'css-loader',
            {
              loader: 'postcss-loader',
              options: { postcssOptions: require('./postcss.config.cjs') },
            },
          ],
        },
        {
          test: /\.(?:png|jpe?g|gif|svg|woff2?|ttf|eot)$/i,
          type: 'asset/resource',
        },
      ],
    },
    plugins: [
      new HtmlWebpackPlugin({
        template: './webpack.html',
        publicPath,
      }),
      new webpack.DefinePlugin({
        __APP_BASE_PATH__: JSON.stringify(appBasePath),
        __API_BASE_PATH__: JSON.stringify(apiBasePath),
      }),
      new ModuleFederationPlugin({
        name: 'opinionRadar',
        filename: 'remoteEntry.js',
        exposes: {
          './App': './src/RemoteApp.jsx',
        },
        shared: {
          react: { singleton: true, requiredVersion: dependencies.react },
          'react-dom': { singleton: true, requiredVersion: dependencies['react-dom'] },
          'react-router-dom': {
            singleton: true,
            requiredVersion: dependencies['react-router-dom'],
          },
        },
      }),
    ],
    resolve: {
      extensions: ['.js', '.jsx'],
    },
    devServer: {
      port: 3026,
      historyApiFallback: true,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, PATCH, OPTIONS',
        'Access-Control-Allow-Headers': 'X-Requested-With, content-type, Authorization',
      },
      proxy: {
        '/api': { target: 'http://127.0.0.1:8008', changeOrigin: true },
        '/remote/opinion-radar/api': {
          target: 'http://127.0.0.1:8008',
          changeOrigin: true,
          pathRewrite: { '^/remote/opinion-radar': '' },
        },
      },
    },
  }
}
